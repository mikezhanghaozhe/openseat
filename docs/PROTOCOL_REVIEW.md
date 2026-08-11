# PROTOCOL_REVIEW.md

Gap analysis of `docs/PROTOCOL.md` v0.1. No implementation code written, no edits made to
PROTOCOL.md itself. Verified against pokerkit 0.7.4 source at
`/Users/zhanghaozhezhang/poker-spike/.venv/lib/python3.12/site-packages/pokerkit/state.py`.

---

## Gap 1 — No trigger for the second hand

**Underspecified.** `POST /rooms/{id}/start` (§6) returns `{ hand_no: 1, to_act, seq }` and the
`hand_complete` event (§5) fires at the end of a hand. Nothing names the call that starts hand 2.
Is `/start` re-callable per hand, or does the server auto-deal the next hand some fixed delay
after `hand_complete`? `make hand` in AGENTS.md ("drives four seats to showdown") only requires
one hand, so this gap is invisible to the M1 gate but blocks anything beyond it — including
`docs/MILESTONES.md`-adjacent work and the `room_complete` event, which has no defined trigger
either (see Gap 2).

**Why it matters.** `arena-client` (agent C) and `scripts/play_hand.sh` both need to know this to
drive more than one hand. Two agents implementing this independently will guess differently: one
might have the room server auto-advance, another might require an explicit host call — and those
produce different event-log shapes for the same room, breaking invariant 5 (`seq` is the basis of
replay) for anyone who assumes the other behavior.

**Options.**
1. `/start` is re-callable; each call deals the next hand if the previous is `hand_complete`.
   Reuses one endpoint, but means `host_token` is required for every hand — awkward for a room
   that's supposed to run autonomously once seated.
2. Server auto-starts the next hand N seconds after `hand_complete` (configurable, maybe via
   `config.turn_seconds` or a new field). No endpoint needed, but "N seconds" needs a number and a
   skip-if-room-closing rule.
3. New `POST /rooms/{id}/next_hand` endpoint, host-gated or auto-fireable, separate from the
   room-lifecycle `/start`.

**Recommendation.** Option 2 with a small fixed delay (e.g. `hand_start_delay_ms` in config,
default ~2000) — matches "the server is authoritative" (invariant 1) better than requiring a human
or script to pull the trigger every hand, and needs no new token-gated endpoint.

---

## Gap 2 — No room-termination condition

**Underspecified.** `room_complete` (§5) carries `{ final_stacks, ranking }` but nothing in
PROTOCOL.md says what causes it to fire. Candidates: all but one seat busted, a configured hand
count reached, the host calling some `/stop` endpoint (which doesn't exist), or all seats
voluntarily leaving. None is specified.

**Why it matters.** This directly gates Gap 4 (busted-seat handling) — you can't know how a busted
seat behaves without knowing whether "down to one seat with chips" ends the room. It also affects
`packages/room-server`'s scheduler: does it keep calling into `GameAdapter.is_terminal` per-hand
and stop the room when the *game* is over, or is termination a room-level policy the adapter has
no say in?

**Options.**
1. Room ends when `GameAdapter.is_terminal(state)` is true (e.g. one seat with chips left) —
   pushes the decision into the adapter, consistent with §9's `is_terminal` existing for exactly
   this.
2. Room ends after a configured `max_hands`, independent of stacks — simpler, deterministic, but
   needs a new config field not in §6's example.
3. Host-triggered `POST /rooms/{id}/stop` — most flexible, but adds a token-gated endpoint with no
   spec for what happens to seats mid-hand when it's called.

**Recommendation.** Option 1: `is_terminal` already exists on `GameAdapter` (§9) specifically to
answer this question — use it, and treat `room_complete` as "the hand loop calls `is_terminal`
after every `hand_complete` and stops if true." Then Gap 1's auto-advance (option 2) and this
compose naturally: advance unless terminal.

---

## Gap 3 — `/start` has no idempotency protection

**Underspecified.** `POST /rooms/{id}/actions` requires `request_id` specifically because
"a dropped response turns into a double raise" (§6). `POST /rooms/{id}/start` has the identical
retry hazard — a client that times out waiting for the response and retries could, depending on
implementation, deal a second hand on top of hand 1's in-progress state — but the endpoint takes
no `request_id` and §6 says nothing about repeat-call behavior.

**Why it matters.** This is the same bug class the protocol explicitly called out and fixed for
`/actions`, just left open one endpoint over. It'll bite exactly when it's least convenient: a
flaky connection during room setup.

**Options.**
1. Require `request_id` on `/start` too, same replay semantics as `/actions`.
2. Make `/start` naturally idempotent: repeat calls while `hand_no >= 1` return the current
   `{ hand_no, to_act, seq }` unchanged rather than dealing again.
3. Leave it — document that `/start` must only ever be called once per room and rely on the host
   token holder being a single trusted caller.

**Recommendation.** Option 2 — cheaper than threading `request_id` through a second endpoint, and
"starting an already-started room just tells you where it's at" is the least surprising behavior.

---

## Gap 4 — Busted seats and the active-player set across hands

**Underspecified.** `status: "busted"` exists in the seat-status enum (§4) but nothing says what
happens to that seat afterward: does the button/blind rotation skip it, is the chair permanently
dead, can a new player claim it via `invite_token`, and does `hand_started.stacks` still carry a
`0` entry for it forever? `GameAdapter.min_players`/`max_players` (§9) bound the *room's* player
count but say nothing about a shrinking *active* set within one room's lifetime.

**Why it matters.** This is one of the six edge cases the task explicitly calls out, and it's not
answerable from the text as written — an implementer has to invent blind-rotation-around-dead-seats
logic with no contract to check it against. Get this wrong and either a busted seat gets dealt in
(violates "server is authoritative" in spirit, wastes cards, may confuse the RNG-reproducibility
story if the dealt-but-unusable hand differs by implementation) or the blind schedule silently
skips over seats inconsistently between agents.

**Options.**
1. Busted seats are permanently dead for the room; blinds/button rotate around them via
   `player_indices`-style filtering; the chair cannot be reclaimed.
2. Busted seats are marked `sitting_out` (already an existing status) and can be revived by a
   rebuy — but M1 has no rebuy endpoint, so this implies scope creep.
3. A room simply ends (Gap 2) once fewer than `min_players` have chips, sidestepping the need for
   long-term dead-seat bookkeeping.

**Recommendation.** Option 1 combined with Gap 2's option 1 (`is_terminal`) — dead seats are
skipped in rotation and the room ends on its own once too few seats have chips to continue, so
"dead seat lives forever" never actually has to be handled indefinitely.

---

## Gap 5 — Minimum occupancy to `/start`

**Underspecified.** §6 shows `POST /rooms/{id}/seats` claiming one seat at a time and `seats: 4`
at room creation, but doesn't say whether `/start` requires all 4 seats filled, or can deal a hand
with some seats still `"open"`. `min_players`/`max_players` on `GameAdapter` (§9) suggests a range
is legal, but the REST layer never surfaces that constraint or an error for violating it.

**Why it matters.** Directly blocks writing `scripts/play_hand.sh`/`make hand` correctly — the
script needs to know if it must wait for 4 `seat_joined` events or can call `/start` after 2.

**Options.**
1. `/start` requires every declared seat (`seats: 4` at creation) to be occupied; open seats block
   start with a new error code.
2. `/start` requires only `GameAdapter.min_players` occupied; open seats are simply excluded from
   the hand (folded/dead for its duration) and can be claimed before the *next* hand.

**Recommendation.** Option 1 for M1 — simpler, matches "REST-first, no LLM yet" scope, and a
board-game-style "wait for everyone" model needs no mid-room seat-claiming logic. Revisit for
later milestones where seats may want to join a running cash-game-style room.

---

## Gap 6 — Uncontested pot ("everyone folds to the BB") is undocumented

**Underspecified.** §3.1 only defines two showdown paths: all contested seats all-in, or at least
one seat with a real show/muck decision. Both assume multiple live seats reach showdown. When
every seat but one folds preflop, there is no showdown at all — pokerkit models this distinctly:
`State.can_win_now`/`win_now` exist specifically for the walk-the-pot case and are not mentioned
anywhere in §10's derivation table, despite being exactly the primitive this path needs.
Also unclear: is a `showdown` event emitted at all in this case (with zero `reveals`), or does the
event log go straight from the last `action_taken` (the fold that ends it) to `pot_awarded`?

**Why it matters.** This is one of the two most common hand endings in poker (the other being a
contested showdown) — "underspecified" here isn't an edge case, it's the *majority* case in most
game trees, and it's also explicitly one of the six required edge-case walks for this review.

**Options.**
1. No `showdown` event when a pot is won uncontested; `pot_awarded` fires with
   `reason: "uncontested"` directly after the winning fold's `action_taken`.
2. Emit an empty/degenerate `showdown` event (`reveals: []`) for consistency, so every hand has
   the same event shape to parse.

**Recommendation.** Option 1, and add `reason`'s enum to §5 explicitly (see Gap 7) — a `showdown`
event with zero reveals is a needless special case for every consumer of the event log to handle.

---

## Gap 7 — `pot_awarded.reason` has no defined values

**Underspecified.** §5's event table lists `pot_awarded: { pots: [{ index, amount, winners: [seat],
reason }] }` — `reason` is a bare string with no enum. At minimum it needs to distinguish
"uncontested" (Gap 6) from "best hand at showdown," and probably a value for split pots (multiple
`winners`).

**Why it matters.** `web/` (agent E) needs to render different UI copy for "won because everyone
folded" vs. "won at showdown" vs. "split." Two agents will invent two different string sets and
the UI will silently mis-render whichever one didn't ship first, with no contract test to catch it
since `tests/contract/` is off-limits to agents to fix.

**Recommendation.** Pin the enum now: `"uncontested" | "showdown" | "split"` (or fold split into a
boolean/derived-from-`winners.length` and keep `reason` to `"uncontested" | "showdown"`). Small,
but exactly the kind of one-line ambiguity that's cheap to close before two agents diverge on it.

---

## Gap 8 — Concurrent-action serialization is assumed, not stated

**Underspecified.** Invariant 1 ("server is authoritative") and invariant 5 (`seq` monotonic)
imply that action application is atomic per room, but nothing in PROTOCOL.md says so explicitly.
Two seats hitting `POST /rooms/{id}/actions` in the same instant is only safe if the room server
serializes state mutation (e.g. a per-room lock or single-writer queue) — since only one seat is
ever `to_act`, a correct implementation rejects the second with 403 `not_your_turn`, but that's
only true if the *check* of `to_act` and the *apply* of the action happen without a race window
where both requests read `to_act` before either commits.

**Why it matters.** `packages/room-server` is the one package built "human, interactively" — this
is precisely the kind of implicit contract that needs to be explicit before someone reaches for
`asyncio` and assumes request handlers are naturally sequenced when they aren't (e.g. multiple
worker processes behind a load balancer, which would break per-room in-memory locking entirely).

**Recommendation.** Add one sentence to §0 or §6: "Actions for a given room are applied one at a
time, in receipt order; the server must serialize `to_act` validation and application per room."
That single line also settles whether horizontal scaling of `room-server` needs a distributed lock
or can shard by `room_id`.

---

## Gap 9 — `request_id` replay when the *body* differs

**Underspecified.** §6 states a repeated `request_id` "returns the *original* result... and does
not re-apply" — but says nothing about what happens when the repeat carries a **different**
`action` body than the first call with that `request_id`. This is the classic idempotency-key
conflict case, and silently replaying the *original* action while discarding a differently-bodied
retry is actively dangerous: a client that fat-fingered a UI double-submit (e.g. retried `fold`
as `call` after editing state) would see `200 { accepted: true }` and reasonably believe the
second body took effect.

**Why it matters.** This is a correctness/trust bug, not a cosmetic one — invariant 4 ("no player
input is trusted") is about validating actions, but this is about the server *silently discarding*
an action it never told the caller it discarded.

**Options.**
1. Silently replay the original result regardless of body mismatch (current implied behavior) —
   simplest, but hides the mismatch from the caller.
2. Return a `409` (new error code, e.g. `request_id_conflict`) when the body doesn't match the
   stored original, forcing the client to generate a fresh `request_id` for a genuinely different
   action.

**Recommendation.** Option 2 — matches the spirit of "raise `IllegalAction` rather than silently
correcting anything" (AGENTS.md style rule) applied to idempotency instead of game rules.

---

## Gap 10 — Turn clock enforcement in M1 (REST-only, no WebSocket)

**Underspecified.** `turn_seconds` is a room-creation config field (§6) and `seat_timed_out` is an
unscoped event (§5), but the turn-clock *mechanism* — "the server starts a timer on
`action_required`... on expiry it applies the forced action" — is written entirely inside §8,
which is explicitly headed "WebSocket (M2)". M1 is REST-only (AGENTS.md: "REST only, no
WebSocket... no LLM"). It's unstated whether a background timer enforcing `turn_seconds` and
firing forced actions runs during M1 at all, or whether the turn clock is inert until M2's push
layer exists.

**Why it matters.** This is the crux of edge case "a seat disconnects mid-hand and reconnects two
streets later" for M1: since REST has no connection object, "disconnect" only has observable
meaning if a server-side timer is independently forcing action on silence. If the timer isn't
active in M1, that edge case literally cannot occur yet (the hand just waits forever for
`POST /actions`) — which is a fine answer, but it should be *stated*, not inferred, since
`scripts/play_hand.sh` and any M1 unit tests need to know whether to expect timeouts at all.

**Options.**
1. Turn clock is active from M1 on — it's a server-side background concern, orthogonal to
   transport. `GET /rooms/{id}/events` lets a REST-only client observe `action_required.deadline_ms`
   and `seat_timed_out` after the fact even without a push channel.
2. Turn clock is genuinely M2-only; M1 hands block indefinitely on `to_act` with no forced action.

**Recommendation.** Option 1 — the config field existing at room creation in the M1-scoped §6
example is a strong signal this was intended, and "REST-only" should describe the transport, not
whether the server enforces its own invariants. Say so explicitly so M1 test-writers know to
exercise it.

---

## Gap 11 — `GET /view` behavior for spectators / missing or wrong token kind

**Underspecified — redaction risk.** §6 defines `GET /rooms/{id}/view?seat_token=...` and returns
an Observation whose shape (§4) unconditionally includes a `you` object. §8 separately says
spectators connect over WebSocket with `?invite_token=...` and get `event` frames only, "never
`state`" — but that carve-out is stated for the *WebSocket* endpoint only. Nothing says what
`GET /view` does when called with an `invite_token` instead of a `seat_token`, with no token at
all, or with a `seat_token` for a different room. Presumably `401 invalid_token` per §7, but §7's
`invalid_token` row is generic ("missing, malformed, or not for this room") and doesn't confirm
this specific case is covered by it rather than falling through to some default-seat rendering.

**Why it matters.** This is the sharpest redaction risk in the whole document, because the failure
mode isn't "leaks nothing" vs "leaks everything" — a naive implementation that defaults to seat 0's
view when no valid `seat_token` is supplied, or that treats any recognized token (including
`invite_token`) as "good enough, render *some* seat," would hand hole cards to exactly the party
invariant 2 exists to stop, through the one endpoint (`GET /view`) that's supposed to be the
primary REST read path in M1 (before WebSocket exists at all).

**Recommendation.** State explicitly: `GET /view` requires a valid `seat_token`; any other token
kind (including a valid `invite_token`) or missing token returns `401 invalid_token`. There is no
REST spectator observation endpoint in M1 — spectators get `GET /rooms/{id}` (public summary) and
`GET /rooms/{id}/events` (public event log) only, matching the WebSocket carve-out in spirit. This
should be a named contract test, not just an inference from §8.

---

## Gap 12 — `text` field redaction has no contract test

**Underspecified — redaction risk.** §4 states `text` "must never contain information absent from
the structured fields — the renderer takes the redacted observation as input, not raw state." This
is a correct rule stated as prose intent, but §10's contract-test checklist (the only enforcement
mechanism named anywhere in the document) has no line item for it. The natural implementation
mistake is exactly the one the sentence is defending against: a renderer function that's handed
"seat 2, plus the full `GameState`" for convenience (e.g. to phrase pot odds or describe the board
more richly) instead of strictly the already-redacted `Observation` object.

**Why it matters.** `text` is the one field whose entire purpose is to be consumed by an LLM seat
that cannot be trusted to *not* act on a leaked card if it's there — this is a higher-stakes leak
surface than a human glancing at raw JSON, because a model seat has no "I won't read that" instinct
and every token of `text` becomes part of its context.

**Recommendation.** Add to §10's contract-test list: "`text` for seat *i* contains no 2-character
card token belonging to any other live, un-mucked seat's hole cards, for every phase." This is
mechanically cheap to test (regex over `text` against the known hidden cards for that observation)
and should live in `tests/contract/`.

---

## Gap 13 — `GET /rooms/{id}/result` shape is unspecified for `showdown`

**Underspecified — redaction risk.** §6 defines `/result` as returning
`{ hand_no, winners, pots, final_stacks, showdown }` with no further shape for `showdown`. If an
implementer takes "give me the result of the hand" literally and serializes whatever internal
struct answered the question — rather than reusing exactly the `showdown` *event's* payload shape
(`reveals: [...]`, populated only for seats that chose `show`) — mucked or folded hole cards could
leak through this endpoint. This is exactly the failure mode invariant 2 calls out ("No endpoint...
may serialize game state by any other path") and `/result` is a plausible place for an agent to
reach for `state.hole_cards` directly since it's explicitly a post-hand summary endpoint.

**Recommendation.** State explicitly that `/result.showdown` is byte-identical in shape to the
`showdown` *event* payload (or is that event payload verbatim, replayed from the log) — never a
fresh serialization of `GameState`. Add a contract test asserting `/result` never contains a hole
card that isn't already present in the room's public event log.

---

## Gap 14 — `seat_timed_out.forced_action` leaks showdown-strength information

**Structural redaction leak, not just underspecified.** §3.1: on a showdown-turn timeout, "the
forced action is `muck` if the seat cannot win any pot, otherwise `show` — never muck a winning
hand on a timeout." §5 makes `seat_timed_out: { seat, forced_action }` a **public** event. Put
together: whenever a non-all-in seat goes silent through their show/muck decision, the *choice the
server made for them* — `show` vs `muck` — is broadcast to the whole room (and any spectator) and
is computed from whether that seat's hand actually wins. A forced `muck` publicly proves "this seat
would have lost," and a forced `show` publicly proves "this seat wins" a beat before the cards are
even revealed (or even if you ignore the subsequent reveal, the *fact* of winning/losing is now
common knowledge for a hand that a human dealer would never announce the outcome of before the
cards hit the felt). This is information a live seat *choosing* to muck a winning hand — legal
under the rules — would never leak (they'd fold their equity but not reveal it was winning), yet an
AFK seat is stripped of that option and the server does it publicly on their behalf.

**Why it matters.** This slips past invariant 2 ("`view` is the only place redaction happens")
because the leak isn't in `view` at all — it's in a *public event* whose payload was designed
without considering that the value it carries (`forced_action`) is itself derived from hidden
information (hand strength vs. the board). No amount of correctly redacting `Observation.hole`
fixes this; the leak is structural to what the event reports.

**Options.**
1. Report `seat_timed_out` without `forced_action`, or with it collapsed to a fact that doesn't
   encode strength (e.g. omit for showdown-phase timeouts specifically, since preflop/flop/turn
   timeouts forcing `check`/`fold` don't have this problem — those are legality-driven, not
   strength-driven).
2. Delay revealing which forced action was taken until the hand's `showdown`/`pot_awarded` events
   fire naturally (batch it), so it's no earlier than a live seat's own choice would have been.
3. Accept the leak as a documented, intentional simplification for M1 (single-table, no ranked
   ladder yet) and revisit before any multi-hand competitive format ships.

**Recommendation.** Option 1, scoped narrowly: keep `forced_action` for non-showdown timeouts
(`check`/`fold` — these are legality facts, already visible via `legal_actions`, not strength
facts) but drop or defer it specifically for showdown-phase timeouts. This is a real design
decision, not a wording fix — flagging it here rather than picking silently.

---

## Gap 15 — §10 derivation table omissions and imprecision (verified against pokerkit 0.7.4 source)

Verified directly against `pokerkit/state.py` (0.7.4, matches the version pinned in the project).

**15a. `state.statuses` and `state.folded_status`/`state.all_in_status` — protocol is correct,
worth confirming.** `statuses: list[bool]` is a genuine per-seat field
(`field(default_factory=list, init=False)`, populated in `__post_init__`, read internally as
`self.statuses[i]`); `folded_status` and `all_in_status` are indeed bare
`bool = field(default=False, init=False)` — single last-operation flags, not arrays. §10's warning
is accurate. (Flagging as verified-correct rather than a gap, since the review was asked to check
this table against the real API and this is the one row worth confirming explicitly rather than
just trusting.)

**15b. `Pot(amount, player_indices)` misdescribes the real constructor.** The actual dataclass is
`Pot(raked_amount: int, unraked_amount: int, player_indices: tuple[int, ...])`. There is no
`amount` constructor argument — `.amount` is a derived `@property` (`raked_amount +
unraked_amount`), which does work for reads, so §10's usage advice ("`state.pots` → `Pot(amount,
player_indices)`") is functionally fine for an adapter that only *reads* `pot.amount`, but is
flatly wrong as a description of the type if anyone ever needs to *construct* a `Pot` (e.g. in a
unit test fixture). More importantly: `raked_amount` implies pokerkit has first-class rake support
that PROTOCOL.md never mentions. §6's room-creation config has no rake field, so `raked_amount`
should always be `0` — but nothing in the protocol states rake is out of scope for M1 and must stay
zero. **Recommendation:** fix the constructor description, and add one sentence confirming rake is
unsupported/forced-zero in M1 so `pot.amount == pot.unraked_amount` always holds and nobody adds a
rake config field later without revisiting this.

**15c. `min_/max_completion_betting_or_raising_to_amount` can be `None`, and §10 doesn't say so.**
Verified: both are `@property` methods that catch `(ValueError, UserWarning)` from
`_verify_completion_betting_or_raising()` and return `None` when raising isn't currently legal for
the actor — not just "small" or "capped," genuinely absent. §10 says "Live properties; re-read
after every action, never cache" but never states they're `int | None`. §3's rule for omitting
`raise` from `legal_actions` ("omitted when the seat is already all-in or `max_to <= min_to`")
doesn't cover the `None` case — comparing `None <= None` (or `None <= int`) raises `TypeError` in
Python, so an adapter written straight from §3's stated rule without reading the pokerkit
docstrings will crash the first time it hits a seat with no legal raise (e.g. call-only situations
in a shorthanded pot). **Recommendation:** add explicitly to §10: "`min_raise_to`/`max_raise_to`
are `None` when raising isn't legal for the actor — treat `None` the same as `max_to <= min_to`
(omit `raise`), and check for `None` *before* the comparison."

**15d. `to_call` has no row in the derivation table at all.** `to_call` appears throughout §3 and
§4 as a load-bearing field but §10 never says what it's derived from. The real primitive is
`state.checking_or_calling_amount` (confirmed present on `State`). **Recommendation:** add
`to_call | state.checking_or_calling_amount` as a row.

**15e. `can_win_now`/`win_now` (uncontested-pot resolution) are absent from §10** despite being the
mechanism Gap 6 needs. **Recommendation:** add a row once Gap 6 is resolved, pointing at
`state.can_win_now`/`state.win_now` as the primitive for the walk-the-pot path.

**15f. "Run it twice" is a live pokerkit feature never mentioned or disabled.** Confirmed present
on `State`: `can_select_runout_count`, `select_runout_count`, `runout_count`,
`runout_count_selection_flag`, `runout_count_selector_indices`. This lets an all-in pot be run out
multiple times with the pot split proportionally across runouts — which can produce fractional
chip amounts before rounding, directly touching invariant 3 ("integers only in the money path").
Nothing in PROTOCOL.md says whether this is enabled, and it's directly relevant to edge case
"3-way all-in on the flop with three different stack sizes." **Recommendation:** add one line to
§10 confirming multi-runout is disabled for M1 (`runout_count` forced to 1), since the wire
protocol (§3, §5) has no concept of a seat choosing a runout count and no event shape for reporting
multiple boards/pots per hand.

---

## Second pass — re-reviewed against the current PROTOCOL.md

Everything above this line was addressed in the document since the first pass: §0.1 now scopes
M1 to exactly one hand and explicitly defers next-hand/room-end/busted-rotation to M2 rather than
leaving them ambiguous (Gaps 1, 2, 4); `/start` is idempotent and gates on `seats_not_filled`
(Gaps 3, 5); §3.0 documents the uncontested-fold path and pins `reason` (Gaps 6, 7); invariant 6
states serialization explicitly (Gap 8); `request_id_conflict` is a real error code (Gap 9); §8
now says plainly that the turn clock is inert in M1 (Gap 10); §6 pins `GET /view`'s token
requirements and the no-spectator-REST-endpoint rule (Gap 11); §10's contract-test list now
includes the `text`-leak and `/result`-leak tests (Gaps 12, 13); §5.1 withholds `forced_action` at
showdown with a named rationale (Gap 14); and §10's derivation table gained `to_call`,
`can_win_now`, the `Pot` constructor correction, the `None`-raise-bound note, and an explicit
forced-off section for rake and run-it-twice (Gap 15). The seed/RNG determinism question I hadn't
yet written up when the first pass shipped is also now closed, and closed *better* than I would
have proposed — see §10 "Seeding": a `master_seed` per room, `derive(master_seed, hand_no)` per
hand, the seed never transmitted at all (not just deferred to `hand_complete` — genuinely never,
for `master_seed`), and a `ARENA_ALLOW_FIXED_SEED` env-gate for the M1 reproducibility tests
instead of a client-suppliable production `seed`.

New findings from this pass follow.

---

## Gap 16 — `host_token`'s "change config" and "kick seats" grants have no endpoints

**Underspecified — internal inconsistency.** §1's Tokens table still states `host_token` grants
"Start hands, **change config, kick seats**." §8 still references the capability directly: "the
seat is held until the room ends **or the host kicks it**." But §6 (REST endpoints, M1) defines
exactly one host-gated endpoint — `/start`. There is no `POST /rooms/{id}/config` and no
`POST /rooms/{id}/kick` (or `/seats/{n}/kick`) anywhere in the document. Either the Tokens table is
describing capabilities that arrive later than M1 and should say so, or two endpoints are simply
missing from §6.

**Why it matters.** This is a smaller version of the exact pattern §0.1 was added to fix for the
next-hand/room-end questions: a capability is *promised* in one section (§1) and *assumed* in
another (§8's kick reference) without ever being *specified* in the section that would let someone
implement it (§6). An agent building `packages/room-server` reading §1 in isolation would
reasonably start scaffolding a config-change endpoint that has no spec for which fields are
mutable, whether it's allowed mid-hand, or what error results from changing `seats` count after
seats are already claimed.

**Options.**
1. Scope §1's Tokens table to what M1 actually ships: `host_token` grants "Start hands" only for
   M1; note "config change and kick arrive in M2" the same way §0.1 does for multi-hand concerns.
2. Add the two missing endpoints to §6 now, minimally scoped (e.g. kick only valid on an `open`
   seat before `/start`, config only mutable before any seat is claimed).

**Recommendation.** Option 1, for consistency with how §0.1 already resolved the analogous gap —
cheaper, and M1's "seats are all claimed before `/start` and the set does not change" (§0.1) makes
mid-room kicking largely moot for this milestone anyway. Update §8's "or the host kicks it" line at
the same time so it doesn't read as an M1-available capability.

---

## Gap 17 — No config-validation error path

**Underspecified.** `POST /rooms` (§6) accepts `seats` and `config` (`sb`, `bb`, `ante`,
`starting_stack`, `turn_seconds`) with no stated validation. §7's error table has no
`invalid_config` (or similar) entry. Concretely unanswered: what happens when `seats` falls outside
`GameAdapter.min_players`/`max_players` (§9)? When `sb >= bb`? When `starting_stack < bb`? When
`turn_seconds <= 0`? `config_schema` (§9) is described only as "rendered as the room-creation
form" — a UI-generation role — leaving open whether the room-server also validates incoming config
against it server-side (invariant 1, "the server decides what happened," implies it must) or trusts
the form to have done so client-side (which invariant 4, "no player input is trusted," directly
contradicts if config counts as input).

**Why it matters.** This is a real hole in the "no player input is trusted" invariant: room
creation is the one point where a caller's numbers flow straight into `GameAdapter.reset(cfg,
seed)` (§9) with no documented check in between. A malformed config (`bb: 0`, `seats: 1`) either
crashes deep inside `packages/game-holdem` on `reset()` — far from the actual bad input, the exact
"delayed mystery crash" pattern §10 explicitly calls out and defends against elsewhere in this same
document for card dealing — or silently produces a room that's unplayable in some other way.

**Recommendation.** Add `400 invalid_config` to §7, and one sentence to §6 stating that
`POST /rooms` validates `config` against the adapter's `config_schema` plus `seats` against
`min_players`/`max_players` before creating anything, returning `400 invalid_config` with the
specific violated constraint. This keeps `config_schema`'s job from being ambiguous between
"suggests a form" and "defines what's legal."

---

## Gap 18 — `GameAdapter.results()` returns `float`, contradicting the integers-only invariant

**Underspecified — real contradiction, not just a missing case.** §9:
`def results(self, s: GameState) -> dict[int, float]: ...`. §2 and AGENTS.md's style section are
unambiguous: "Chips. Integers only. No floats anywhere in the money path," "Integers only in the
money path. No floats anywhere near chips." `results()` is the one `GameAdapter` method whose job
is explicitly to report an outcome in the money path (results at hand/room end, feeding
`final_stacks`/`ranking` in `hand_complete`/`room_complete`, §5), and its type signature is a bare
`float`.

This is plausibly intentional at the `GameAdapter` level — the protocol is explicit that "poker is
game one, not the product," and a future non-chip game might have a genuinely fractional result
(win probability, normalized score, a draw scored `0.5`). But nothing says that, and for
`game-holdem` specifically, an implementer has to *decide*, unguided, whether `results()` returns
raw integer chip counts upcast to `float` (harmless but pointless), a stack *delta* (`stack_after -
starting_stack`, still integer-valued but typed as `float`), or a normalized return (genuinely
fractional, e.g. `stack_after / starting_stack - 1`) — and that choice determines whether anything
downstream that consumes `results()` for poker is allowed to do float arithmetic on it, which is
exactly what invariant/style rule is trying to prevent near chips.

**Options.**
1. Keep `float` at the `GameAdapter` protocol level (games plural, some may be fractional) but add
   one sentence to §9 or §10 pinning `game-holdem`'s specific return: integer chip delta, `float`
   only because the interface is generic — never fed into further arithmetic, only rendered.
2. Make `results()` return `dict[int, int]` for now, since M1 has exactly one game and it's
   chip-denominated; widen to `float` only when a second, non-chip game actually needs it.

**Recommendation.** Option 1 — the `GameAdapter` protocol is deliberately game-agnostic (§9's
header line says so) and narrowing its type now to fit poker alone fights that design goal. But say
explicitly, next to the `results()` line or in §10, what `game-holdem` puts in that `float` and
that nothing may treat it as money to be added/compared with `<`/rounded — the ambiguity, not the
type, is the actual gap.

---

## Gap 19 — `room_closed` scope: does it 410 the read endpoints too?

**Underspecified.** §7: `410 room_closed — Room finished or expired`. §0.1 now makes this concrete
and immediate for M1: a room finishes after exactly one hand's `hand_complete`. What isn't stated
is which endpoints return `410` once a room is closed. If it's all of them — including
`GET /rooms/{id}/view`, `GET /rooms/{id}/events`, and `GET /rooms/{id}/result` — then immediately
after the one hand M1 plays, the room becomes unreadable through the API, which conflicts with §5's
"the event log **is** the transcript... replay = re-read it" (re-read *how*, if the read endpoint
itself 410s?) and with `GET /rooms/{id}/result` (§6) existing at all — a result endpoint that stops
answering the moment there's a result to give is a strange contract. If `410` is scoped only to the
mutating endpoints (`/start`, `/actions`), that's a much more sensible reading but it isn't the one
a literal reading of "Room finished or expired" in §7 suggests, and needs to say so.

Also unstated: "or expired" implies a room TTL for a room that never fills its seats or never
calls `/start` — no such TTL, nor what happens to its (still live, still secret) `seat_token`s and
`host_token` on expiry, appears anywhere in the document.

**Why it matters.** `scripts/play_hand.sh`/`make hand` (AGENTS.md gate) presumably wants to fetch
`GET /rooms/{id}/result` right after driving the hand to completion — if that 410s because the room
is already `room_closed` by the time the script gets to reading it, the M1 gate itself is
unsatisfiable as literally specified.

**Recommendation.** State explicitly that `410 room_closed` applies only to state-mutating
endpoints (`/start`, `/actions`); `GET /view`, `GET /events`, and `GET /result` remain readable
indefinitely (or at least for a documented retention window) after closure — reads are exactly what
"the event log is the transcript" is promising. Separately, either define a room-expiry TTL for
never-started rooms or state that M1 rooms don't expire and cleanup is out of scope for this
milestone (matching how §0.1 handled the other deferred questions).

---

## Minor items (not worth a full section)

- **Chat is only sendable alongside an action.** `table_talk` appears exclusively as a field on the
  action request (`POST /actions`, §6) and the WS `act` frame (§8) — there's no standalone
  chat-only call. A seat can therefore only speak on its own turn (or during its own showdown
  `show`/`muck` decision). Given `chat`/`table_talk` are treated as first-class in the Observation
  shape and event table (§4, §5), this reads like an accidental restriction rather than a chosen
  one. Worth a one-line confirmation either way.
- **`GameAdapter.reset(cfg, seed)` — which seed?** §10 now describes a `master_seed` per room and
  `derive(master_seed, hand_no)` per hand, owned at the engine layer. `reset()`'s `seed: int`
  parameter (§9) doesn't say whether it receives the room's `master_seed` or the already-derived
  per-hand seed. Given `GameAdapter` is meant to be engine-agnostic to seeding strategy, it's almost
  certainly the latter — but "almost certainly" is doing the work a one-line docstring should.
