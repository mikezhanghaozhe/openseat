# DECISIONS.md

Append-only. One entry per non-obvious choice. Newest at the bottom.

Format: `## YYYY-MM-DD — short title` / **Decision** / **Why** / **Consequence**.

If you are an agent and you made a judgement call that another agent could reasonably have made
differently, add an entry. This file is the only way context crosses package boundaries.

---

## 2026-08-09 — Rules engine is PokerKit, not our own

**Decision.** Use `pokerkit` (MIT, UofT CPRG) for poker rules and hand evaluation rather than
implementing them.

**Why.** Side pots, all-in-for-less, and min-raise legality are the highest-density bug area in
poker and the most expensive to debug under time pressure. PokerKit already handles them and is
well tested.

**Consequence.** `pokerkit` may only be imported from `packages/game-holdem/`. Everything else
sees `GameAdapter`. Attribution obligations recorded in `THIRD_PARTY_NOTICES.md`.

---

## 2026-08-09 — Spike findings, pokerkit 0.7.4

Six questions answered by running code against pokerkit 0.7.4. All six are now binding on
`packages/game-holdem/` and are documented in `docs/PROTOCOL.md` §10.

**1. `complete_bet_or_raise_to(n)` is raise-TO.** Confirmed by stack delta.
→ The wire format uses `to` semantics with no conversion at the adapter boundary.

**2. Side pots arrive pre-split.** `state.pots` is a list of `Pot(amount, player_indices)`, one per
stack tier, each carrying its own eligible-seat list. No remainder math required.
→ Protocol §4 exposes `pots[]` directly rather than a `{main, side[]}` shape.

**3. Showdown discretion is conditional.** When every contested seat is all-in, showdown resolves
automatically regardless of the `HOLE_CARDS_SHOWING_OR_MUCKING` flag — there is no decision left
to make. The manual path only engages when a non-all-in seat could choose to muck; there
`actor_index` is `None` and we drive `show_or_muck_hole_cards(show, player_index)` in any order
via `showdown_indices`.
→ Protocol §3.1 makes showdown a turn phase with `show` / `muck` actions.

**4. Min/max raise-to are live properties**, recomputed after each action. Max is effectively the
actor's stack, so all-in-for-less is accepted with no special call.
→ Never cache them. Re-read after every action when building `legal_actions`.

**5. We own the RNG.** But `deal_hole` / `deal_board` want **one concatenated string per call**
(`"AsKd"`), not a list of 2-char strings. Passing a list silently stores raw `str` instead of
`Card` objects and crashes later inside hand evaluation, far from the cause.
→ Confined to a single `_deal()` helper with a post-condition assert. Contract test added.

**6. `committed_street` / `committed_hand` do not exist.** Derive from `state.bets[i]` and
`state.starting_stacks[i] - state.stacks[i]`.
→ **Trap:** `state.folded_status` and `state.all_in_status` are single last-operation booleans,
not per-seat arrays. The per-seat list is `state.statuses`. Misusing these presents as a rules bug
and is very hard to trace. Derivation table in PROTOCOL.md §10.
→ **Second trap (added during protocol review):** `starting_stacks[i] - stacks[i]` is only valid
mid-hand. Once chips are pushed at hand end it goes negative for winners. Snapshot before award or
gate the field on `phase != "hand_complete"`.

---

## 2026-08-09 — REST before WebSocket

**Decision.** M1 ships the full game over plain HTTP (`GET /view` + `POST /actions`). WebSockets
are added in M2 as a push layer over the same contract.

**Why.** The engine should not depend on the transport, and a curl-drivable surface is far easier
to test and debug. Every later client — WS, arena-client, MCP bridge, webhook — wraps a surface
that already works.

**Consequence.** The `state` WebSocket frame and the `GET /view` response must stay byte-identical
in shape. If they diverge, that is a bug.

---

## 2026-08-09 — `arena-client` is an internal package first

**Decision.** Build `packages/arena-client/` in M1 as the client used by contract tests, model
seats, and the MCP bridge. Publishing it is an M5 packaging step, not a build item.

**Why.** Three parts of our own system need a client to our own room. Writing it once avoids three
subtly different reconnect and `seq`-dedupe implementations.

**Consequence.** `web/` needs a separate TypeScript client (`web/src/lib/room.ts`) — two languages,
not two designs. Generate shared types from the protocol schemas so they cannot drift.

---

## 2026-08-10 — Seed is secret during a hand

**Decision.** The per-hand seed is withheld from every client-facing payload until the
`hand_complete` event. The room's `master_seed` is never transmitted at all.

**Why.** An earlier draft of PROTOCOL.md recorded the seed in the public `room_created` event.
Anyone holding the seed and knowing the shuffle algorithm can compute every hole card at the
table — a total break of hidden information. Caught during protocol review, before implementation.

**Consequence.** Replay still works, because replay only needs the seed after the hand is over.
Explicit seeds for tests require `ARENA_ALLOW_FIXED_SEED=1`, off in production. Three contract
tests added in PROTOCOL.md §10.

---

## 2026-08-10 — Credentials in headers, WebSocket via short-lived ticket

**Decision.** `seat_token` and `host_token` travel in `Authorization: Bearer`, never in query
strings. WebSocket connections use a single-use 30-second ticket from `POST /rooms/{id}/ws-ticket`.

**Why.** Query strings are written to proxy logs, browser history, and `Referer` headers, and a
`seat_token` is long-lived. Browsers cannot set headers on a WebSocket handshake, so a short-lived
ticket is the standard way to keep the durable credential out of the URL.

**Consequence.** Clients need one extra round trip before connecting. `arena-client` hides this;
`web/src/lib/room.ts` must implement it too.

---

## 2026-08-10 — Protocol review triage (15 gaps)

A local adversarial review of PROTOCOL.md v0.1 produced 15 findings (`docs/PROTOCOL_REVIEW.md`).
Resolution:

**Scoped out.** Gaps 1, 2, 4 — next-hand trigger, room-termination condition, busted-seat
rotation. All three are multi-hand problems. Declaring "an M1 room plays exactly one hand" (§0.1)
removes them rather than designing them speculatively. They return in M2 with real requirements.

**Fixed in v0.1.** Gaps 3, 5, 6, 7, 8, 9, 11, 12, 13, 14, 15b–f. See PROTOCOL.md sections
§0 (serialization), §0.1 (scope), §3 (`None` bounds), §3.0 (uncontested pot), §5/§5.1 (event
payloads), §6 (`/start`, `/view`, `/actions`, `/result`), §7 (new error codes), §10 (derivation
table, forced-off features, contract tests).

**Rejected.** Gap 10 — the reviewer recommended enforcing the turn clock from M1. Declined: a
background timer firing during REST-only contract tests makes them nondeterministic, which fights
invariant 3. `turn_seconds` is accepted at room creation but inert until M2. Stated explicitly in
§8 so M1 test-writers do not expect timeouts.

**Two findings worth remembering.**

*Gap 15c* — `min_/max_completion_betting_or_raising_to_amount` return `None`, not a small integer,
when raising is illegal. §3's original rule (`max_to <= min_to`) would have raised `TypeError` the
first time a seat could only call. Found by reading pokerkit's source rather than its docs.

*Gap 14* — `seat_timed_out.forced_action` at showdown is computed from hand strength and broadcast
publicly, proving a seat was beaten before any card is revealed. This leak **does not pass through
`view()`**, so invariant 2's single chokepoint cannot catch it. General lesson recorded in §5.1:
before adding a field to a public event, ask what hidden state its *value* is derived from. A
redaction chokepoint protects serialized state, not derived values.

---

## 2026-08-10 — Origin check and WSS deferred

**Decision.** Cross-Site WebSocket Hijacking protection (`Origin` allowlist at the handshake) and
enforced WSS are out of scope for the MVP.

**Why.** Not needed for a demo; WebSockets bypass CORS entirely so this is a real gap, but it is
not on the critical path to a playable table.

**Consequence.** Must be closed before any public deployment. Recorded here so it is a deferral,
not an oversight.

---

## 2026-08-10 — Third review round: fixes and a real bug

Two further reviews produced ~30 findings. Applied in full except where noted.

**The bug.** §3's `legal_actions` rule said to omit `raise` when `max_to <= min_to`, while the very
next line described all-in-for-less as a raise with `min_to == max_to` — the first sentence deleted
exactly the case the second described. A short stack's legal all-in raise would have been missing
from `legal_actions`. Also corrected: `max_raise_to` is `stacks[i] + bets[i]` (total street
capacity), not the remaining stack. Verified against pokerkit 0.7.4.

**Self-contradiction that broke the M1 gate.** §0.1 closes a room after one hand; §7 said a closed
room returns 410. `make hand` reads `/result` right after that hand, so the gate was unsatisfiable
as written. Reads (`/`, `/view`, `/events`, `/result`) now never 410; only `/start` and `/actions`
do.

**`ts` vs determinism.** Invariant 3 promised a byte-identical event log while every event carries
a wall-clock timestamp. Narrowed: identical excluding `ts`, which the room server stamps outside
the adapter.

**`state.pots` is an iterator.** Deriving `pots[]` and `pot_total` from two reads yields
inconsistent output. Materialize `tuple(state.pots)` once per construction.

**Odd chips.** `pot_awarded.winners` cannot express a split where one winner gets an extra chip.
Replaced with `awards: [{seat, amount}]`, which is now authoritative.

**Table talk is not a leak.** A seat saying "I have Ah Kd" is legal poker — speech play, and
truthfulness is not required. The hidden-information invariant constrains the *server*, not the
players; narrowed to "hidden information the server derived." Explicitly rejected chat filtering:
bypassable, false-positive prone, and it would make a secrecy boundary out of something that isn't.

**Canonical event order (§5.0) added.** Determinism is untestable without one. Also separated the
public event log from the private hand record — the earlier text implied the seed store and the
transcript were the same thing, which would have leaked the deck.

**`can_win_now(seat)` means less than it sounds.** It compares against already-*exposed* hands, not
every hidden hand. That happens to be exactly right for the showdown timeout rule — it matches what
a dealer could determine — but must not be read as "this seat wins."

**Rejected.** `expected_seq` preconditions on actions. With a single per-room queue, a request from
a seat that is not `to_act` is rejected when it reaches the front; the pre-submission race the
reviewer describes cannot occur. Not worth the client complexity.

**⚠️ Unresolved — settle by test.** The two reviews disagree on whether PokerKit auto-resolves
all-in showdowns without `HOLE_CARDS_SHOWING_OR_MUCKING`. §10 now specifies the version that is
correct either way: explicitly loop `showdown_indices`. An adapter test settles it.

---

## 2026-08-10 — Review is closed

Three review rounds is enough. Further passes go to `tests/contract/`, not to PROTOCOL.md — a spec
gap that no contract test can express is not a gap worth closing before code exists.

---

## 2026-08-10 — Reduction pass, not an addition pass

Round three left the document self-inconsistent in three places and padded in several more. This
pass fixed and deleted; it added nothing.

**Contradictions removed.**
- §10's automation note said to drive show/muck "in whatever order you like," while §3.1 requires
  `showdown_indices` order exactly. Reveal order changes what later seats know, so it is not a free
  choice. §10 now defers to §3.1.
- `/start` returned `seq` in §6 but the `/actions` section claimed it returns `first_seq`/`last_seq`.
  Now the pair, everywhere.
- The `showdown` event's `reveals` array did not say how it relates to §3.1's one-event-per-decision
  rule. Now stated: one entry per discretionary show, all live hands at once in the all-in case.

**Deleted as noise.** Changelog sentences explaining what earlier drafts got wrong ("That was a
bug") — that history belongs here, not in a spec someone reads to implement from. The timing
side-channel scope note, which was not actionable at MVP. §5.1's rationale, cut from three
paragraphs to the rule it exists to state.

**Deduplicated.** `committed_hand` carried two overlapping instructions merged badly; `/result` said
"never a fresh serialization of `GameState`" twice; there were two separate "Contract tests this
implies" lists. §5.1 also sat before §5.0.

**Standing principle for this document.** A spec is read to implement from, not to learn the
project's history from. Rationale earns its place only when it prevents a specific mistake — "why
`forced_action` is withheld" stays because someone would otherwise add the field back. Everything
else goes here.

---

## 2026-08-10 — Replay artifact is the deck, not a seed

**Decision.** Drop `master_seed` and `derive()`. The room holds one `room_seed`; at the start of
each hand the room server draws one complete 52-card shuffle and passes the deck to
`GameAdapter.reset(cfg, deck)`. The **deck** is published in `hand_complete`. `room_seed` is never
published.

**Why.** Two independent reasons, both better than the scheme they replace.

*Robustness.* A seed only reproduces a deck if the identical shuffle code runs — same algorithm,
same RNG, same Python version. Refactor the shuffle and every stored seed silently replays a
different hand, with no error to notice. A stored deck doesn't need the algorithm; it is the
answer. The cost is 52 strings per hand.

*Simplicity.* `derive()` existed solely because publishing hand 3's seed would otherwise leak the
master and therefore hands 4 through 10. Publishing decks removes the problem rather than solving
it — hand 4's deck isn't derived from anything that's been disclosed. That deletes a cryptographic
requirement (HMAC, one-wayness) and a shuffle-algorithm-pinning requirement from the spec.

**Consequence.** `GameAdapter.reset` takes `deck: list[str]` instead of `seed: int`; the adapter
never shuffles. Seeded RNG survives only as a *testing* tool via `ARENA_ALLOW_FIXED_SEED`
(invariant 3), not as the replay mechanism.

**One constraint this creates.** Draw a whole shuffle per hand, never card-by-card on demand. Lazy
drawing makes the number of RNG calls depend on how the hand played, so hand 2's cards would depend
on how hand 1 *went* — which breaks the "same deck, different model" comparison that is the point
of replay. Contract test added.

**Credit where due.** This came from the question "couldn't we just store the actual cards?" The
seed scheme was the conventional answer, not the right one for this project; compactness was never
a constraint here.

---

## 2026-08-11 — room-server: `GameAdapter.reset` can't produce the hand-start events (flag for §9)

**Gap found.** §9's `reset(cfg, deck) -> GameState` returns only state, but §5.0's setup sequence
needs `hand_started`, game-specific postings (`blinds_posted` for holdem), `hole_cards_dealt`, and
`action_required` immediately after. `room_created`/`seat_joined` are room-server-generic and don't
need the adapter; `blinds_posted` content (who posted what) is genuinely game-specific and cannot
be synthesized from a generic `Observation` — there is no `postings` field in §4.

**Decision (room-server-local, not a PROTOCOL.md change).** Added `GameAdapter.setup_events(state)
-> list[Event]`, called once immediately after `reset`, to the copy of the `GameAdapter` Protocol
that lives in `packages/room_server/adapter.py`. It returns the full hand-start sequence with
placeholder `seq`/`ts`, same convention as `apply`. `StubAdapter` implements it.

**Flagging for the PROTOCOL.md owner.** This is exactly the kind of gap AGENTS.md says to flag
rather than resolve unilaterally across a package boundary — §9 is frozen and owned by the human.
Whoever builds `packages/game-holdem` will need this method too, so either §9 gains it formally or
an equivalent mechanism is specified before that package is built. Interim state: room-server's
local Protocol copy is a superset of §9, not a divergent one — every method §9 specifies is
implemented exactly as specified; this is the only addition.

**Related, same root cause.** §9's `reset(cfg, deck)` also has no way to receive the seat count
(needed by every adapter, not just the stub — a hand can't be dealt without knowing how many
players are in it). Room-server passes it as a reserved `cfg["_seats"]` key, injected *after*
config validation against `config_schema` so it never has to appear in the schema itself. Same flag
applies.

---

## 2026-08-11 — room-server: adapter `view()` doesn't own the whole `Observation`

**Decision.** `GameAdapter.view(state, seat)` is invariant 2's sole redaction chokepoint, but four
fields it returns are overwritten by the room server before the response goes out:
`protocol_version`, `seq`, `room_id` (server-owned envelope, the adapter has no way to know any of
them), and seat `name`/`kind` on `you` and every entry in `seats[]` (join-time metadata owned by
the room server's seat registry, not visible to the adapter). `chat` is similarly built by the room
server from its own event log, not returned meaningfully by the adapter.

**Why this doesn't violate invariant 2.** The adapter still owns every field where redaction
judgment is actually exercised — hole cards, stacks, pots, legal actions, status, `to_call`/raise
bounds. The overlay only ever replaces fields that carry zero hidden information and that the
adapter structurally cannot populate (it never receives room/seat identity, only a bare seat
index). No new serialization path is opened; the overlay works purely by
`dataclasses.replace` on the adapter's own return value.

**Consequence for `packages/game-holdem`.** Its `view()` can return `""`/`0`/placeholder
name+kind for those overlaid fields — the room server will overwrite them on every call. Documented
in `adapter.py`'s docstring so this isn't rediscovered per-adapter.

---

## 2026-08-11 — room-server: idempotency replay is checked before the closed/turn gates

**Decision.** `POST /actions`' `(room_id, seat, request_id)` lookup happens first, ahead of the
`room_closed` and `not_your_turn` checks — not after them.

**Why.** §6 says idempotent replay exists so a dropped response doesn't turn into a double action;
that has to include the response to the action that *closes* the room (e.g. the fold that ends the
only hand in M1). Checking `closed` first meant a client retrying its own closing action's
`request_id` — because it never saw the first response — got `410 room_closed` instead of its
original result, defeating the point of idempotency for the single most likely case of a dropped
response (the room closing is exactly when a client is most likely to be racing a reconnect).
Caught by an end-to-end curl smoke test before any contract test ran, not by mypy or ruff — this
class of bug lives entirely in request ordering.

**Consequence.** A genuinely new `request_id` still hits `room_closed`/`not_your_turn` exactly as
before; only a replay of an already-reserved id bypasses them. Test:
`test_idempotent_replay_survives_room_close` in `tests/unit/test_room_server.py`.

---

## 2026-08-11 — room-server: `GET /events?since=N` is exclusive, not inclusive

**Decision.** `/events?since=N` returns events with `seq > N`, matching the WebSocket `resume.since`
semantics in §8 exactly (both mean "I already have up to and including `since`").

**Why.** §6 says a polling client "passes `since=last_seq` ... and knows it has seen every
consequence of its own request" — i.e. it already has everything through `last_seq` and wants only
what comes *after*. That only makes sense under exclusive semantics; inclusive would re-serve events
the client already has. This makes `/events?since=0` (shown as the illustrative example in §6)
skip the very first event, `seq=0` — a real oddity, but a documented and intentional one. A client
that wants the full log from the start passes a `since` below the first `seq` (any negative
number works, since `seq` starts at 0).

**Flagging for the PROTOCOL.md owner.** §6's own example (`?since=0`) reads as if it means "give me
everything," which is the inclusive interpretation — that's the ambiguity being resolved here.
Worth a one-line clarification in §6 itself.

---

## 2026-08-11 — game-holdem: the §10 all-in/showdown ambiguity, settled by test

**Decision.** §10 flagged a real disagreement between two protocol reviews about whether pokerkit
auto-resolves an all-in showdown without `Automation.HOLE_CARDS_SHOWING_OR_MUCKING` enabled. Settled
empirically against pokerkit 0.7.4, not by reading its docs: it does **not**. With that automation
(and `HOLE_DEALING`/`BOARD_DEALING`) excluded, `state.showdown_indices` sits populated and
`can_deal_board()` stays `False` until every seat in it has been explicitly shown or mucked —
nothing advances on its own.

**The real operation order, verified by stepping through pokerkit's source
(`_end_bet_collection`/`_begin_showdown`/`_end_showdown`), is also not what the two-review
disagreement assumed.** For an all-in hand, showdown reveals happen **before** the remaining board
is dealt, one street's worth of dealing at a time, cycling `showdown → deal → showdown(empty) →
deal → …` until the last street, only then falling into hand-killing. It is not "deal the whole
board, then reveal everyone." `_advance` in `adapter.py` mirrors this exactly (`can_deal_board()`
checked first each loop iteration, `showdown_indices` second) because that is the order pokerkit
actually requires, not the more intuitive TV-broadcast order.

**Consequence.** The wire event order this produces (`showdown` bundling all reveals happens right
before the final `board_dealt`+`pot_awarded`+`hand_complete`, even though pokerkit resolved the
*reveal itself* earlier, before most of the board was dealt) is a deliberate choice: `_finalize`
defers constructing the wire `showdown` event until the board is fully known, since hand
rank/description needs it — see the next entry for why that deferral turned out to be load-bearing
for a different reason. §5.0 doesn't pin down board-dealt-vs-showdown ordering for the pure all-in
case (only for the discretionary one), so this is within the protocol's freedom, not a deviation
from it.

---

## 2026-08-11 — game-holdem: three real bugs found only by driving real hands, not by mypy

`mypy --strict` and `ruff` were clean the entire time these existed. All three were caught by
running actual hands through the adapter and checking the money added up — the discipline AGENTS.md
asks for ("no swallowed errors," "raise rather than silently correct") doesn't help when the bug is
in trusting a library's return value that looks reasonable but means something narrower than it
appears to.

**Bug 1 — `Pot` is a mutable dataclass; `tuple(state.pots)` doesn't protect you from that.**
`push_chips()` decrements `pot.unraked_amount` on the *same object* as it distributes each sub-pot.
Capturing `pots_before = tuple(pk.pots)` before pushing looks like a snapshot, but a tuple only
freezes which objects it holds, not their contents — by the time `PotAward.amount` was read from
it, every pot showed `0`. Fix: extract plain ints (`[p.amount for p in tuple(pk.pots)]`) immediately,
before any `push_chips()` call. The two-fold trap here: §10 already warns that `state.pots` is an
*iterator* that must be materialized once — true, but materializing it isn't enough on its own,
because what you materialize is still a list of objects the engine keeps mutating.

**Bug 2 — `state.pots` (and hence `pot_total`) is `()` for an entire betting round until it
closes.** Confirmed empirically: right after blinds are posted, before any voluntary action,
`tuple(state.pots)` is empty even though 75 chips are sitting on the table. `state.bets[i]` holds
the current street's uncollected commitments separately, swept into `pots` only at bet collection.
`Observation.pot_total`/`pots[]` must add `sum(state.bets)` on top of `state.pots` or it silently
under-reports for most of a hand — the §4 example's `pot_total: 450` implies "everything in the
middle right now," which `state.pots` alone does not give you.

**Bug 3 — `Automation.HAND_KILLING` destroys a losing hand's hole cards before you can report
them, and this only shows up in a 3-way-or-more all-in.** `kill_hand()` → `_muck_hole_cards()` calls
`self.hole_cards[player_index].clear()` on every seat that `can_win_now()` says can't win, as
ordinary end-of-hand cleanup — real-life poker dealer behavior, modeled faithfully, but it means
that by the time `_finalize()` runs (after the board is fully dealt, which is required to know who
lost), the losing seats' `state.hole_cards` are already empty lists. `state.get_up_hands()` then
returns `None` for them — not stale data, a clean `None`, which reads exactly like "this seat's
hand was never evaluable" rather than "this seat's hand was deleted after being evaluated." A
heads-up hand never exposes this (only one seat can lose, and the winner's own cards are never
killed, so `hole=[...]` for the single loser still happened to be captured earlier in the code as
written) — it takes 3+ seats going all-in together to reveal it, which is exactly the scenario the
critical §10 test in the previous entry exercises.

**Fix.** Stopped depending on `state.get_up_hands()` and `state.hole_cards` for reveal construction
entirely. Each all-in-revealed seat's hole cards are captured into
`GameState.pending_all_in_reveals` (a `dict[seat, hole_cards]`) *at the moment* `show_or_muck_hole_cards(True, seat)`
is called — before hand-killing gets anywhere near it — and hand strength is computed later, once
the board is final, via `StandardHighHand.from_game(hole, board)`: a standalone evaluator that takes
cards directly and has no dependency on `State`'s internal bookkeeping (or its side effects) at all.
The discretionary (non-all-in) show path was changed the same way for consistency, even though it
wasn't independently broken (its `_reveal` call already happened before `_advance()` could trigger
hand-killing).

**How this was caught.** Not a design review — a deliberate decision (documented in AGENTS.md's own
workflow guidance and repeated in this task's instructions) to smoke-test the adapter with real
multi-seat hands, across many random seeds, checking `sum(awards) == pot.amount` and
`sum(results.values()) == 0`, before writing a single formal test. The first 3-way all-in tried
threw `AssertionError: no evaluable hand for seat N` immediately. A type checker cannot catch "this
library method returns `None` for a reason that isn't the one you assumed" — only running the code
can.

---

## 2026-08-11 — game-holdem: button is seat (n-1), not seat 0

**Decision.** `GameState.button = seats_total - 1`, derived directly from how pokerkit assigns
blind postings to seat indices — not hardcoded to `0`.

**Why.** Verified empirically: for 3+ players, `raw_blinds_or_straddles=(sb, bb)` passed
positionally lands `sb` on seat index `0` and `bb` on seat index `1`, with the first voluntary
actor at seat `n-1` (UTG in a 3-handed hand is the button, since after BB the action returns to the
button before wrapping to SB) — i.e. button = `n-1`. Heads-up is the interesting case: pokerkit
*reverses* the blind assignment there (seat `0` gets `bb`, seat `1` gets `sb`) to match the
standard rule that the button posts the small blind heads-up — and seat `1` is still `n-1`. The
formula holds for every player count without a special case, which is why it's trusted rather than
patched around.

**Consequence for §4's `button: 0` example.** That's an illustrative value from one example hand,
not a requirement — nothing in PROTOCOL.md pins the button to a specific seat, and M1 never rotates
it (§0.1, one hand per room), so reporting pokerkit's own natural convention honestly is simpler and
less error-prone than remapping seat indices to force button on seat 0.

---

## 2026-08-11 — game-holdem: uncontested-fold `pot_awarded.amount` is the full pot, not pokerkit's

**Decision.** When a hand ends by everyone-but-one folding, `PotAward.amount`/`awards[]` reports
the seat's **entire winnings** (both blinds, e.g. `75`), computed directly from `sum(pot amounts) +
sum(state.bets)` at that moment — not from `push_chips()`'s returned `ChipsPushing.amounts`, which
for this one specific case (`sum(state.statuses) == 1`, pokerkit's fast path for "only one player
left, nothing to compare") only reports the *net transfer* (`25`, excluding the winner's own
last bet, which just gets swept back to them via `pull_chips()` instead of being formally
"pushed").

**Why.** Both numbers are internally self-consistent on their own terms (`sum(awards) ==
pot.amount` holds either way) — the choice is about which one matches what the client already saw.
`Observation.pot_total` shows `75` throughout the hand (see the pot_total bug two entries up); if
`pot_awarded` then reported `25`, a client watching the hand would see the pot shrink by two-thirds
at the exact moment it's "awarded," with no event explaining where the other `50` went. Reporting
the full amount keeps `pot_total` and `pot_awarded.amount` consistent across the one transition
where a client would notice if they weren't.

**Scope.** This special-case only applies when `not hand_had_showdown` (the uncontested path,
exactly one pot, exactly one winner by construction — no side-pot splitting question can even arise
here). A genuine multi-way showdown never hits this shortcut in pokerkit — verified against a 3-way
all-in, where `push_chips()`'s reported amounts already summed to the true full pot with no
adjustment needed — so the showdown path trusts pokerkit's own accounting unmodified.

---

## 2026-08-11 — game-holdem: a genuine side pot cannot occur in any M1 hand

**Finding, not a decision — recorded because it constrains what's testable and because a future
agent could reasonably expect otherwise.** §6's `POST /rooms` config carries exactly one
`starting_stack` for the whole room. Every seat's *total capacity for the hand* is
`stack[i] + bets[i]`, which stays equal to that one shared `starting_stack` for every seat, for the
entire hand, right up until someone's chips actually leave the table — which only happens at
`hand_complete`, by definition the end. Side pots require unequal maximum contributions among
contesting seats; with a uniform `starting_stack` and only one hand ever played per room (§0.1),
that inequality can never arise, regardless of bet sizing or street. Confirmed by trying: a
plausible-looking construction (small flop raise from two seats, all-in raise from a third) still
produces exactly one pot, because the two "smaller" bettors' *ceiling* was never actually lower —
they simply hadn't yet chosen to commit the rest of their equally-sized stack.

**Consequence for testing.** `test_3way_unequal_stacks_produce_more_than_one_pot_with_correct_eligibility`
in `tests/unit/test_game_holdem.py` builds a `GameState` directly with `NoLimitTexasHoldem.create_state`
and per-seat stacks, bypassing `HoldemAdapter.reset()` (which only accepts the single uniform
value), specifically because the room-server's actual config path cannot reach this scenario. The
adapter's pot-splitting *logic* is still real production code being tested — only the room-shaped
entry point is bypassed, to reach a state §6's config can't construct in M1.

**Where this actually matters.** M2's multi-hand rooms are exactly what makes stacks diverge (a
player wins or loses a previous hand, then a later hand's all-in has real inequality) — this
isn't a defect to fix now, just scope worth naming so nobody spends M1 time chasing a side-pot bug
report that can't reproduce from a fresh room.

---

## 2026-08-11 — game-holdem: `Automation.RUNOUT_COUNT_SELECTION` is unnecessary, not just unused

**Decision.** Omitted entirely from `_AUTOMATIONS`, rather than included-but-forced-to-1.

**Why.** `create_state` defaults to `mode=Mode.TOURNAMENT` (used explicitly here, not left implicit,
for documentation), and `State._begin_showdown` only ever offers runout-count selection when
`mode != Mode.TOURNAMENT`. In tournament mode, `can_select_runout_count()` is `False` for the entire
hand — there is nothing for the automation to do, and `state.runout_count` simply never gets set to
anything other than its default. §10's "force runout_count to 1" requirement is satisfied
structurally by the mode choice, not by an automation that would otherwise need to reject a
selection request that tournament mode never lets a caller make in the first place.

---

## 2026-08-11 — game-holdem: pokerkit's own internal `deck_cards` is inert here, and warns about it

**Not a bug, recorded so a future agent doesn't chase it as one.** `NoLimitTexasHoldem.create_state`
builds its own internally-ordered `state.deck_cards` (52 cards) regardless of what's passed to it —
there is no parameter to suppress this. Since `HOLE_DEALING`/`BOARD_DEALING` are excluded from
`_AUTOMATIONS` and every card dealt here comes from an explicit string built from the room server's
seeded shuffle (`cards.deal_hole`/`cards.deal_board`), `state.deck_cards` is never read from — but
pokerkit still checks each explicitly-dealt card against it and emits `UserWarning: A card being
dealt ... is not recommended to be dealt` whenever it doesn't match pokerkit's own unused internal
ordering, which is effectively always. Confirmed harmless: every money/card invariant checked across
30+ random-seed hands holds regardless. Left unsuppressed deliberately — silencing warnings globally
here risks hiding a real future one.

---

## 2026-08-12 — arena-client: sync HTTP, not async

**Decision.** `RoomClient` wraps a plain `httpx.Client`, not `httpx.AsyncClient`.

**Why.** M1 is REST-only and every call is a single request/response with no need to hold a
connection open (§8: WebSocket, where concurrency actually matters, arrives in M2). `scripts/
play_hand.py` — this package's own primary consumer today — is a straight-line script; async would
buy it nothing but a `asyncio.run` wrapper. `httpx.MockTransport` and `httpx.Client`/`AsyncClient`
share the same transport interface, so nothing about today's tests or wire logic would need to
change to add an async variant later.

**Consequence.** M3 (model seats) and M5 (MCP bridge) may want concurrent seats/tools and will
likely want an async client. Deferred rather than guessed at now — building both today would mean
maintaining two request/response/parse paths for a need that isn't concrete yet. Flagging this
explicitly so M3/M5 doesn't assume async support exists.

---

## 2026-08-12 — arena-client: explicit per-type parsers, not a generic decoder

**Decision.** `parse.py` is ~15 small functions (`parse_observation`, `parse_event`, one per
`Payload` member, …), each naming its fields explicitly, rather than one reflective
dict-to-dataclass walker driven by `dataclasses.fields()`.

**Why.** This is the reverse operation of `packages/room_server/serialize.py`'s `to_wire` (which
*is* generic, because every dataclass follows one rule: omit `None`). Decoding doesn't have that
luxury — a generic decoder still needs to know, per field, which enum to construct, which nested
type to recurse into, and which `EventType` maps to which `Payload` member. That dispatch table
ends up exactly as long as these explicit functions, just written once as data instead of code, and
loses `mypy --strict`'s ability to check each field access against the real dataclass signature.
Explicit functions cost more lines; they cost zero `Any`.

---

## 2026-08-12 — arena-client: only two dedicated exception subclasses

**Decision.** `IllegalActionError` and `RequestIdConflictError` are the only `ArenaApiError`
subclasses. The other nine §7 error codes (`invalid_token`, `not_your_turn`, `room_not_found`,
`seats_not_filled`, `seat_taken`, `room_full`, `hand_in_progress`, `room_closed`, `rate_limited`)
all raise the base `ArenaApiError` directly, distinguishable via `.error`.

**Why.** The task names exactly one required behavioral difference — "on 409 illegal_action, do not
blindly retry; re-read legal_actions and surface it" — and request_id_conflict has an equally sharp
shape (retrying with the same id can never succeed; it's a caller bug, not a transient failure).
Every other code is informational to the caller in the same way: "here's what went wrong, here's
the reason." A dedicated class per code would be nine near-empty subclasses adding ceremony without
adding a behavioral distinction anyone's been asked to make yet.

---

## 2026-08-12 — arena-client: `_request` treats a non-JSON error body as recoverable, not fatal

**Bug, found by a test, not by writing it defensively up front.**
`test_malformed_error_body_still_raises_a_usable_error` (constructed to simulate a raw-text 500 —
the kind an upstream proxy or a crashed process produces, not our own JSON error shape) crashed
with an unhandled `json.JSONDecodeError` from inside `response.json()`, before `_request` ever got
the chance to build an `ArenaApiError`. The fix: `_request` now catches the decode failure and falls
back to `{"reason": response.text}`, so every non-2xx response — ours or not — raises the same
`ArenaApiError` type. This was the one test in the suite written from "what could actually go wrong
against a real network," not from the wire spec directly, and it's the one that caught something.

---

## 2026-08-12 — scripts/play_hand.sh starts its own wired server; `make dev` still doesn't have holdem-nl

**Decision.** `scripts/_serve_holdem.py` builds its own app via
`packages.room_server.main.create_app(adapters={"holdem-nl": HoldemAdapter()})` and runs it with
`uvicorn.run(...)` directly, rather than `play_hand.sh` shelling out to `uvicorn
packages.room_server.main:app` (`make dev`'s command).

**Why.** `packages/room_server/main.py`'s module-level `app` — what `make dev` actually serves —
registers only `StubAdapter` in its adapter registry (`packages/room_server/adapter.py`'s own
`create_app` default). That's correct and expected: `room_server` was built and is owned
independently of `game-holdem`, has no reason to import it, and `create_app(adapters=...)` exists
specifically as the seam for exactly this kind of external wiring. Discovered by trying to run
`play_hand.sh` against a server started the `make dev` way first — every room creation failed with
`400 bad_request: unknown game 'holdem-nl'`, correctly, because the real game genuinely isn't
registered there.

**Consequence — flagging, not fixing.** `make dev`'s server still can't play a real hand of poker
today; only `scripts/_serve_holdem.py`'s instance can. This isn't a bug in room_server (it's doing
exactly what it says), but it does mean the two milestones' pieces aren't wired together anywhere
except here, and only for the duration of this one script's run. Worth a real decision — likely
`room_server` growing an adapter-registry entry point read from config or an env var, once
`game-holdem`'s ownership settles — rather than staying implicit in a script under `scripts/`.
`scripts/_serve_holdem.py` uses `room_server`'s own public extension point and edits nothing under
`packages/room_server/`, so it stays inside this task's ownership boundary while still producing a
genuinely runnable end-to-end gate.

---

## 2026-08-12 — a real game-holdem bug found via play_hand.sh, fixed after explicit user go-ahead

**Originally left unfixed on purpose** — flagged per AGENTS.md's own rule ("if your task needs a
change in someone else's package, stop and flag it — do not reach across"), even though the fix had
already been written and verified. The ownership boundary that session was scoped to
(`packages/arena-client/` and `scripts/play_hand.sh` only) meant `packages/game_holdem/` wasn't
mine to edit without being asked. **Fixed in a follow-up turn after the user asked directly** —
their go-ahead is the authority the per-task ownership boundary was standing in for; it isn't an
override of the underlying rule, it's who the rule was deferring to.

**The bug.** In `packages/game_holdem/adapter.py`'s `_apply_showdown_decision`, hole cards for a
`show` decision are captured via `[repr(c) for c in s.pk.hole_cards[seat]]` **after** calling
`s.pk.show_or_muck_hole_cards(show, seat)`. For every showdown decision except the *last* one
pending, that's fine. For the last one, `show_or_muck_hole_cards` itself cascades synchronously
through pokerkit's internal `_end_showdown` → `_begin_hand_killing` chain before returning (
`Automation.HAND_KILLING` is enabled) — the exact same class of bug already documented in this file
under "game-holdem: three real bugs found only by driving real hands" for the *all-in* reveal path,
just reachable one call earlier than that entry accounts for. If the last-to-show seat turns out to
hold a losing hand, hand-killing clears `state.hole_cards[seat]` before the capture line runs, and
the `showdown` event — and therefore `/result`'s `showdown[]`, which is a faithful projection of
that same event, correctly per §6 — reports `hole: []` for a seat that explicitly chose to show.

**How it was found.** Not by a game-holdem unit test — none of the 21 tests in
`tests/unit/test_game_holdem.py` happen to reach a *discretionary* (non-all-in) showdown with 3+
seats where the last-to-decide seat both shows and loses; the closest existing test is heads-up,
where the second-to-act seat mucks rather than shows, so the affected code path is never exercised.
It surfaced immediately in a real 4-seat run through `scripts/play_hand.sh` — three of four seats
showed correct hole cards, the fourth (last to act) came back empty every time the scenario was
close to reproduced.

**The fix, applied.** Moved the hole-card capture line above the `show_or_muck_hole_cards` call,
matching the pattern already used in `_advance`'s all-in branch (`packages/game_holdem/adapter.py`,
the `if pk.all_in_status:` branch) — capture unconditionally before the call, then only build the
`Reveal` from it if `show` is `True`. Verified as the complete fix both ways: `pytest
tests/unit/test_game_holdem.py` (21 passed) and `mypy --strict packages/game_holdem/` stayed clean,
and `scripts/play_hand.sh` rerun three times produced correct, non-empty hole cards for every one of
the four seats in every run (previously only seat 3, the last to act, came back empty).

**Why `scripts/play_hand.py` still doesn't assert on hole-card contents in `/result`.** It wasn't
written to catch this specific bug, and adding an assertion narrowly tuned to a bug that's now fixed
would be scope creep for a gate script whose job is "did the hand complete over HTTP." Worth noting
for whoever next touches `game_holdem` or `room_server`: this bug was invisible to every contract
test, unit test, and HTTP status code — it needed a real 4-seat run with printed output a human (or
this conversation) actually read to notice. That's a gap in test coverage, not just a one-off fixed
bug: a `/result` hole-card round-trip test in `tests/unit/test_game_holdem.py` covering a 3+-seat
discretionary showdown where the *last* seat to decide both shows and loses would have caught this
directly, and doesn't exist yet.

---

## 2026-08-13 — M1 gate verification pass: two real bugs fixed, the whole system finally wired together

**Context.** Asked directly whether the project meets M1 (docs/MILESTONES.md's 5-bullet gate),
authorized to cross package boundaries as needed to find out. Running the full suite for the first
time against the real, fully-wired stack (room_server + game_holdem together, not each in
isolation) surfaced two real production bugs and a batch of contract-test bugs. Recorded together
here since they were all found in one pass and the fixes interact.

**Bug 1 (real, fixed) — a bad config crashed `/start` with an unhandled 500 instead of failing at
`POST /rooms`.** `HoldemAdapter`'s `sb < bb` / `starting_stack >= bb` checks lived only inside
`reset()`, called from `/start`, not from room creation — exactly the failure mode §6 explicitly
warns against ("a bad config must fail here, not crash inside reset() far from the cause").
Confirmed by reproduction: `POST /rooms` with `sb=50, bb=25` returned `201`, both seats could claim,
and `POST /start` then threw a raw `ValueError` up through FastAPI as an unhandled 500 with a full
stack trace in the response — never caught anywhere. This was invisible before this pass because no
existing test exercised the full create→claim→start sequence with an invalid config against the
real adapter; `tests/unit/test_game_holdem.py`'s own config tests call `reset()` directly and
correctly expect `ValueError` there, which is a different (and still correct) claim than "the API
never crashes."

**Fix.** Added `validate_config(cfg) -> None` to the `GameAdapter` protocol
(`packages/room_server/adapter.py`) — not in §9, same category of interim extension as
`setup_events` (see the 2026-08-11 entry) — implemented as a no-op in `StubAdapter` and as the real
`sb`/`bb`/`starting_stack` checks in `HoldemAdapter`, factored into a shared
`_check_cross_field_config` helper also still called defensively from `reset()` itself. `room_server`'s
`RoomStore.create_room` now calls it right after `config_schema` validation, converting a
`ValueError` into `400 invalid_config` at room-creation time. This is the room-server-side follow-through
on the JSON-Schema-can't-express-cross-field-constraints gap flagged in the 2026-08-11 "game-holdem:
uncontested-fold `pot_awarded.amount`" cluster of entries — that gap is now closed, not just
documented.

**Bug 2 (compatibility gap, fixed) — `GameState` didn't support the `state.X` access pattern §10
itself uses throughout.** §10's whole derivation table is written as `state.hole_cards[i]`,
`state.pots`, `state.bets[i]`, `state.can_win_now(seat)` — direct attribute/method access matching
raw `pokerkit.State`. `HoldemAdapter.reset()` returns a `GameState` wrapper instead (needed for the
bookkeeping §10 itself also requires — "maintain our own per-seat status... rather than deriving it
fresh each view" — which raw `pokerkit.State` can't hold), and nothing generalized that access
pattern through the wrapper. This wasn't reachable from production code (room_server never touches
`GameState` internals — invariant 2 — and `game_holdem`'s own code always writes `s.pk.X`
explicitly), but it meant every one of `tests/contract/test_adapter_*.py`'s tests written against
§10's literal notation (before `game_holdem` existed) failed with `AttributeError`, not because the
adapter was wrong, but because the wrapper never exposed the surface those tests — and §10's own
prose — assume exists.

**Fix.** Added `GameState.__getattr__`, delegating any name `GameState` doesn't define itself to
`self.pk`. Two attributes needed more than plain delegation and got explicit `@property` overrides
instead, because pokerkit's raw values are actively misleading for a caller reading them *after* a
hand resolves:
- `pots` — `pk.pots` is drained to `0` by `push_chips()` once the hand resolves (see the "three real
  bugs" entry above, Bug 1) — a post-hoc reader gets zeros, not what was actually paid out.
  `GameState.pots` now returns a pre-drain snapshot captured in `_finalize`, falling through to the
  live value mid-hand.
- `runout_count` — stays `None` for the entire hand in `Mode.TOURNAMENT` (see "`Automation.RUNOUT_COUNT_SELECTION`
  is unnecessary" above) even though the guarantee it represents (single runout) always holds;
  `GameState.runout_count` now reports `1` instead of leaving a caller to infer that from `None`.

**What this fixed, concretely.** `tests/contract/test_adapter_*.py` went from 2/16 passing to 14/16
(the 2 still failing are contract-test bugs, not adapter gaps — see below).

**The room-server default app now serves real poker.** `packages/room_server/main.py`'s
`_default_adapters()` registers `HoldemAdapter` alongside `StubAdapter` whenever `packages.game_holdem`
is importable (plain `try/except ImportError`, no hard dependency — room_server still never imports
`pokerkit`, only optionally imports game_holdem's adapter class). Previously only
`scripts/_serve_holdem.py`'s one-off wiring could play a real hand; now `make dev`, `create_app()`
with no arguments, and every test that imports `packages.room_server.main.app` directly all get the
real game. This is the change flagged as future work in the "scripts/play_hand.sh starts its own
wired server" entry above — done now, not deferred.

**Eleven contract-test bugs found. Six fixed after explicit user authorization scoped to exactly
one class of change; five flagged, not fixed — per AGENTS.md ("if a contract test looks wrong, stop
and say so rather than editing it").** All eleven are test-authoring mistakes, confirmed by direct
reproduction against the real system, none are adapter or room-server bugs:

- *Six* API tests (`test_repeating_request_id_with_different_action_returns_409_conflict`,
  `test_retrying_request_id_with_different_table_talk_does_not_conflict`,
  `test_illegal_action_does_not_reserve_its_request_id`,
  `test_actions_response_last_seq_equals_highest_seq_it_emitted`,
  `test_result_returns_200_after_close_and_409_before_hand_complete`,
  `test_canonical_setup_and_action_event_order_matches_protocol`) hardcoded `seat_tokens[0]` as the
  first actor. In heads-up hold'em the button/small blind (seat 1 in a 2-seat room, per the
  "button is seat (n-1)" entry above) acts first preflop, not seat 0 — every one of these failed
  with `403 not_your_turn` at the first action, confirmed by direct reproduction. **Fixed**: the
  user explicitly authorized this one specific class of change ("if the only changes you need is to
  replace hardcoded seat_tokens[0] to be flexible"). Added one local helper,
  `_first_to_act_token(client, room_id, seat_tokens)`, to `test_api_contract.py` — it reads `to_act`
  from any seat's `/view` (a pure read, available regardless of whose turn it is) and returns that
  seat's token — and replaced every `seat_tokens[0]` used as an acting token with it, consistently
  within each test (both the first and any retried/conflicting call use the *same* resolved token,
  since idempotency identity includes the seat — see §6). No assertion, scenario, or expected
  outcome in any of the six tests changed; only which token submits the action did. All 15 tests in
  `test_api_contract.py` pass now. `test_no_m1_payload_contains_m2_reserved_fields`, which has the
  identical `seat_tokens[0]` pattern, was already passing before this fix and needed no change — it
  never asserts on the fold's status code, so it happened to be robust to the bug.
- *Two* adapter tests reach for a side pot or a "no legal raise" scenario using `_cfg(3,
  starting_stack=...)` — a uniform `starting_stack`, which the "a genuine side pot cannot occur in
  any M1 hand" entry above already proves can never produce either scenario. Not a new finding, the
  same structural limit documented there, just two more tests that assumed it wasn't a hard
  constraint.
- *One* adapter test (`test_seat_status_matches_derivation_table_for_fold_all_in_and_active`)
  hardcodes `remaining[1]` as "the seat to shove," without checking whose turn it actually is.
  Confirmed by direct reproduction: after the scripted fold, seat 0 (not seat 1) is next to act, so
  `legal_actions(state, 1)` correctly returns `[]` and the test's `next(a for a in legal if
  a.type==RAISE)` raises `StopIteration`. Same bug class already found and fixed in this package's
  own `tests/unit/test_game_holdem.py::test_seat_status_across_fold_active_and_all_in`, which uses a
  dynamic `_to_act()` lookup instead of a guessed index for exactly this reason.
- *Two* leak tests have genuine test-logic bugs, not false alarms about redaction:
  `test_no_deck_or_card_appears_in_any_surface_before_hand_complete` calls `setup_room` (create +
  claim + start only) and then asserts the event log already contains `hand_complete` — no action is
  ever submitted, so the hand is still waiting on its first decision; the assertion cannot pass
  regardless of adapter correctness.
  `test_no_hole_card_leak_across_any_observable_surface_all_phases_all_seats`'s `_blob` helper
  concatenates every seat's *own* view (fetched with their own token) into one string and then
  checks that seat's own hole cards don't appear anywhere in it — which flags a seat correctly
  seeing its own cards as a "leak." A corrected version of this exact check (excluding each seat's
  own view from what's checked against their own cards) was run directly against the live
  `holdem-nl`-wired server as part of this pass and found zero leaks.

**Independent verification of the actual M1 gate (docs/MILESTONES.md), not the contract-test pass
rate.** The gate is 5 specific bullets; the contract suite tests considerably more than that
(§10 pokerkit-internals-level detail) and inherited bugs of its own predate this pass. Each gate
bullet was checked directly against the fully-wired system:

1. `scripts/play_hand.sh` drives 4 seats to showdown through HTTP only — re-run 3× after every fix
   in this pass, exit 0 every time, all four seats' hole cards correct.
2. Leak test (no seat's view contains another seat's hole cards, any phase) — verified with a
   corrected version of the buggy contract test's logic, run live against the wired server: zero
   leaks across a full 3-seat hand.
3. Determinism (same seed + actions ⇒ identical event log) —
   `test_same_seed_same_actions_produce_identical_log_excluding_ts` passes against the real adapter.
4. Illegal action returns 409, turn pointer unchanged — verified live: an out-of-bounds raise
   returns `409 illegal_action`, and `to_act` is identical before and after.
5. Side pot: 3-way all-in with unequal stacks pays out correctly — verified live for money
   conservation and awards-sum-to-pot-amount on an API-reachable 3-way all-in, and independently for
   genuine stack inequality (not reachable via `POST /rooms`'s single `starting_stack`, per the
   entry above) via `tests/unit/test_game_holdem.py`'s adapter-level test, which constructs unequal
   stacks directly.

**All 5 pass.** M1's gate is met by the real, fully-wired system as of this pass — not by an
isolated package's own tests, and not by `scripts/_serve_holdem.py`'s one-off wiring, which no
longer needs to exist separately from `make dev` now that Bug 2's fix folded the same wiring into
`room_server`'s own default.

---

## 2026-08-14 — `starting_stacks`: per-seat stacks in room config

**Decision.** `POST /rooms` accepts `config.starting_stacks` (one entry per seat) as an alternative
to `config.starting_stack`. Exactly one of the two must be present. Not gated behind any flag.

**Why.** Two contract tests — the 3-way tiered all-in and the no-legal-raise case — were
unreachable. The reasoning, verified against the adapter: `reset()` passes
`tuple([starting_stack] * seats_total)` to pokerkit, so every seat starts equal. Within one hand a
`call` matches the current bet exactly, so any two live seats have always contributed identical
amounts and hold identical remaining capacity. Three different stack tiers therefore cannot arise,
and neither can "two seats all-in while a third still has chips behind" — a third seat calling goes
all-in at the same instant. Driving to unequal stacks through legal actions alone is provably
impossible, not merely awkward.

**Why it is not a test backdoor.** Unequal stacks are the ordinary state of a real table — someone
doubles up, someone rebuys short. M1 only lacks them because it plays exactly one hand from a fresh
room. So this is a legitimate config field that happens to unblock the tests, unlike
`ARENA_ALLOW_FIXED_SEED`, which is a genuine test-only facility and stays flag-gated.

**Consequence.** Without it, side pots — the highest-risk path in the engine and the main reason
for depending on PokerKit — would have shipped M1 entirely unverified. Three contract tests added.
Validation: length equals `seats`, entries integer `>= bb`, both fields present is
`400 invalid_config`.

---

## 2026-08-14 — room-server: zero `Observation` construction sites, via `GameAdapter.waiting_view`

`Room._waiting_observation` in `store.py` built the pre-`/start` `Observation` by hand — a second
`Observation(...)` call site in `packages/room_server/`, alongside the one inside `Room.view`'s
delegation to `adapter.view`. It was harmless today (every field runs empty, before any cards
exist), but it broke the property that made invariant 2 grep-checkable: "exactly one place
constructs `Observation`" degrades from a fact you can verify with `grep -rn "Observation(" ` to a
fact you have to re-derive by reading both sites and reasoning about why the second one happens to
be safe. That reasoning does not survive the next person adding a spectator view or a between-hands
state to the same function.

**Fix.** Added `GameAdapter.waiting_view(cfg, seats, seat)` to §9 (not in the original protocol
text, like `setup_events`/`validate_config` before it) and moved the waiting-observation logic
behind it, implemented identically in `StubAdapter` and `HoldemAdapter`. `store.py` now only ever
calls `adapter.view` or `adapter.waiting_view` and passes the result through the same `_overlay`
used for the in-hand path — no bespoke envelope construction left in `store.py`.

**Why `SeatJoinedPayload` for the `seats` param, not a new type.** It already has exactly
`seat`/`name`/`kind` — the only fields the waiting view needs — so introducing a dedicated type
would duplicate it for no gain.

**Scope note.** `grep -rn "Observation(" packages/room_server/` still returns hits in `stub.py`:
`StubAdapter.view` and `StubAdapter.waiting_view`. Those are legitimate — `StubAdapter` is a
`GameAdapter` implementation that happens to live inside `packages/room_server/` rather than its
own `packages/game-*` package (it predates `game-holdem` and nothing has moved it since), so its
adapter methods are the same kind of construction site `HoldemAdapter.view` is in
`packages/game_holdem/`, just not filtered out by a path-based grep. The invariant the grep is
meant to check — "the room server itself never builds an `Observation`" — now holds; the grep
needs a smarter filter (e.g. exclude adapter implementation files, or grep for the pattern outside
adapter classes) to say so on its own. Flagged rather than silently worked around.

---

## 2026-08-14 — game-holdem: live bets belong in `pot_total`, not as a synthetic `pots[]` entry

`pk.pots` (PokerKit) only reflects streets already swept by bet collection — it is `()` for the
entire preflop round even with both blinds posted, confirmed empirically (`test_preflop_blinds_
are_in_pot_total_but_not_in_pots`). `HoldemAdapter.view` used to paper over this by appending a
synthetic `PotView` for `sum(pk.bets)` whenever it was nonzero, so a viewer's "chips in the middle
right now" wouldn't under-report during a live street.

**Why that was wrong.** `pots[].index` is not decorative — §5's `pot_awarded` event references pot
indices, so a client uses them to line up which pot resolved. The synthetic entry was not a real
pot: it hadn't been divided into tiers yet (a raise-and-multiple-calls can still fragment into a
side pot once someone goes all-in), and once it resolved into real settled pots, every index after
it would shift. An index that means one thing mid-hand and another at award time is exactly the
kind of bug a client can't defend against.

**Fix.** `pots[]` now contains only `tuple(pk.pots)`, unchanged from settlement to settlement —
indices are stable and match what `pot_awarded` will later reference. `pot_total` is `sum(settled
pot amounts) + sum(pk.bets)`, so it still reports the live total continuously through a hand; the
uncollected-bets number just moved from one existing field to the other, no new `Observation`
field. `pot_total` is provably unchanged at the instant a street closes and PokerKit sweeps `bets`
into `pots` — same chips, relabeled — pinned by
`test_pot_total_is_unchanged_when_a_street_closes_and_bets_sweep_into_pots`.
---

## 2026-08-26 — room-server: M2 WebSocket layer — one writer, one clock, flagged determinism fallout

**Shared code path (invariant 6).** `Room.submit_action` (REST) and the new `Room.submit_action_for_seat`
(WS `act`) both resolve their seat differently but then call the same `_commit_action`, extracted
from the old `submit_action` body unchanged. This is also what makes "the same `request_id` over
REST and then WS does not double-apply" free: idempotency is keyed by `(seat.index, request_id)` in
`Room.idempotency`, which neither knows nor cares which transport wrote it.

**Turn clock is now enforced over REST too, not just WS.** §8 says "now enforced" without gating by
transport, so `Room._stamp` overwrites every adapter's placeholder `ActionRequiredPayload.deadline_ms`
(always `0` from both `StubAdapter` and `HoldemAdapter` — an inert M1 placeholder) with a real,
room-server-computed absolute epoch deadline, and a background `asyncio.Task` (`Room._run_timer`)
enforces it regardless of whether any socket is even connected. A monotonic `timer_generation`
counter is bumped on every arm/cancel; a timer that wakes up after being superseded checks it under
`self.lock` and no-ops instead of double-applying — this, plus the lock itself, is the entire
"a timeout wins any race" guarantee: there is no window where a late timer and a concurrent action
can both commit.

**Determinism-test fallout — flagged, not silently patched.** `deadline_ms` being real means
`tests/unit/test_room_server.py::test_same_seed_same_actions_same_log_excluding_ts` needed the same
treatment already given to `ts` (strip it before comparing) — that test is this package's own
scratchpad, so it was updated directly. **`tests/contract/test_api_contract.py::
test_same_seed_same_actions_produce_identical_log_excluding_ts` needs the identical one-line fix
(strip `action_required.deadline_ms` alongside `ts`) and was deliberately left failing rather than
edited**, per AGENTS.md ("do not modify anything under tests/contract/... if a contract test looks
wrong, stop and say so") and this task's own instruction to flag rather than silently change REST-
adjacent behavior. This is not a REST *response shape* regression — the field and its type are
unchanged — only a previously-static placeholder becoming genuinely wall-clock-derived, exactly
parallel to `ts`'s existing exemption. Flagged for the PROTOCOL.md/tests/contract owner.

**Showdown-timeout forced action is duck-typed, not a hard `GameAdapter` member.** §3.2 wants "muck
only if the seat cannot win any pot, otherwise show," which needs PokerKit's `state.can_win_now`
— a primitive that lives only inside `packages/game_holdem`, out of reach behind invariant 2's opaque
`S` TypeVar. Adding it as a *required* `GameAdapter` protocol member (in this package's own
`adapter.py`, following the `setup_events`/`waiting_view` precedent) would still require
`packages/game_holdem`'s `HoldemAdapter` to implement it for `mypy --strict packages/` to pass —
out of scope for this task (ownership map: game_holdem is agent B's). `Room._forced_showdown_action`
instead does `getattr(self.adapter, "can_win_now", None)`: if present and callable, uses it; if
absent (as for every adapter today), defaults to `show`. That default still satisfies the hard
invariant ("never muck a winning hand on a timeout") since always showing can never incorrectly
forfeit a winner — it just doesn't get the muck-optimization until `game_holdem`'s owner adds the
hook. Flagged for that owner and the PROTOCOL.md owner to promote to a real (optional) protocol
member if/when a game needs the full rule.

**Broadcast never holds the lock across socket I/O.** Each WS connection owns one `asyncio.Queue`
registered via `Room.subscribe()`; `Room._broadcast` does a synchronous `put_nowait` per subscriber
while `self.lock` is held (never awaits), and a per-connection pump task drains its queue onto the
actual socket. A slow or dead client can therefore never stall a room mutation or another client's
delivery; ordering per subscriber is preserved because enqueue happens in event order under the lock.

**`hello` always replays from seq 0; `resume` is the real incremental reconnect path.** The ticket
carries no "since," so a fresh connect can't know what a client has already seen — `hello.replay` is
unconditionally the full log, and §8's explicit "clients must tolerate duplicates and dedupe on seq"
is exactly what covers the overlap with a subsequent `resume {since}`, which does the actual atomic
"replay since+1..latest_seq, then state as of exactly that seq" under one lock acquisition.

---

## 2026-08-26 — protocol: `can_win_now` promoted to a required `GameAdapter` member; two M2 fixes

**`GameAdapter.can_win_now(s, seat) -> bool` is now §9, not a duck-typed room-server hook.** The
previous entry's `getattr(self.adapter, "can_win_now", None)` fallback defaulted to always `show`
when absent — safe against "never muck a winner," but *unsafe* the other direction: it never mucked
a genuinely losing hand either, which is the entire point of the rule. A disconnected seat holding a
beaten hand would have had its cards forced face-up into `showdown`'s `reveals[]` and the public
event log, permanently, the first time any adapter besides `HoldemAdapter` (which never implemented
the hook) was ever put behind a room needing showdown-timeout enforcement. Required, not optional,
closes that hole for good — `packages/game_holdem/adapter.py`'s `HoldemAdapter.can_win_now` delegates
straight to `state.pk.can_win_now(seat)`; `StubAdapter.can_win_now` returns membership in `s.active`
(the stub has no showdown phase, so this is never actually consulted, but the protocol contract
still needs an implementation). `Room._forced_showdown_action` (`packages/room_server/store.py`) now
just calls `self.adapter.can_win_now(self.state, seat)` directly — no `getattr`, no default.

**PokerKit's `can_win_now` compares against already-*exposed* hands only, never every hidden hand.**
This is exactly right for the forced-timeout rule — it matches what a dealer could determine at that
moment, since only exposed information is fair to act on — but must never be read as "this seat is
the winner." A seat can pass `can_win_now` (nothing revealed yet beats it) and still lose once a
later seat's hand comes up; the rule only ever asks "is this seat *already* provably beaten," not
"is this seat destined to win." See §9/§10 and `test_showdown_timeout_forces_muck_for_a_seat_that_cannot_win`.

**Invariant 3 now excludes `deadline_ms` alongside `ts`.** The M2 turn clock made
`action_required.deadline_ms` a real, room-server-stamped wall-clock value (previously every
adapter's inert `0` placeholder) — exactly the same category of non-determinism `ts` was already
carved out for, and for the identical reason: stamped outside the adapter, from wall-clock time, and
it doesn't affect game outcome. `docs/PROTOCOL.md` §0 invariant 3 and the §10 checklist were updated
first, per AGENTS.md ("if the document is wrong, change it here first, then fix the code") — only
then was `tests/contract/test_api_contract.py::
test_same_seed_same_actions_produce_identical_log_excluding_ts_and_deadline_ms` (renamed from
`..._excluding_ts`) updated to strip `deadline_ms` alongside `ts` and match the new invariant text.

**`test_pots_are_materialized_once_per_view_not_read_twice` was asserting the wrong invariant, not
catching a real bug.** Diagnosed by direct inspection of `HoldemAdapter.view()`: `pots =
tuple(pk.pots)` is materialized exactly once per call, and `pot_total = sum(settled pot amounts) +
sum(pk.bets)` is the documented, deliberate design (see the "live bets belong in pot_total" entry
above) — `pk.pots` is genuinely `()` for an entire street until it closes, so `pot_total ==
sum(pots[].amount)` is false by design the instant blinds are posted, which is exactly the state the
test checked. No double-iterator-read exists in the adapter. Fixed the test to check what it actually
meant to guard: `pots[]`/`pot_total` must not drift across repeated `view()` calls with nothing else
happening (the real double-read symptom — a live iterator silently draining on a second read), plus
the documented formula (`pot_total == sum(pots) + sum(committed_street across seats)`) explicitly,
instead of the unconditional-equality assumption that was never true to begin with.

**M3 model seats: driven from `Room._after_events`, reusing `timer_generation` as the staleness
token.** When a fresh `action_required` names a seat that has a `ModelSeat` registered
(`Room.model_seats`), `_after_events` spawns a background task the same way `_arm_timer` already
does for the turn clock — same pattern, same "keep a strong ref or asyncio may GC the task" reason
(`self._model_tasks`, mirroring `self.timer_task`). The task reads the seat's `Observation` under
`self.lock`, releases it, awaits the provider (which can take seconds), then resubmits through
`submit_action_for_seat` — the identical `_commit_action` path a human uses. Reusing
`self.timer_generation` (already bumped on every `_arm_timer`/`_cancel_timer`) as the "has this turn
already moved on" check avoids inventing a second generation counter for what is the same "is this
still the live turn" question the turn clock already answers.

**A model seat's `ApiError(NOT_YOUR_TURN)` is swallowed, not logged as a bug.** The task spec calls
this out explicitly: the turn can legitimately move on between the observation read and the
resubmit (a human acted first, or the turn timer forced an action first), and that must read as an
ordinary race, not an error. Any *other* `ApiError` from that resubmit is logged — it would mean the
seat's own decided action was rejected for a reason unrelated to turn ordering, which would be a
real bug in `policy.validate` disagreeing with the adapter's own `legal_actions`.

**API-key resolution happens at `POST /seats` time, not on first turn.** `Room._build_model_seat`
calls `agent_runtime.driver.resolve_api_key` synchronously inside `claim_seat`, so a `key_mode:
"house"` seat claimed with no `OPENROUTER_API_KEY` set, or a `key_mode: "byok"` seat claimed with no
`api_key`, fails the seat claim with `400 bad_request` immediately — never a mysterious failure on
the seat's first turn, and never a fallback to a wrong key.

**`ModelSeat`'s 15s decide-timeout is read from the module global at construction time, not bound
into the constructor's default-argument value.** A `def __init__(..., timeout_seconds: float =
DEFAULT_TIMEOUT_SECONDS)` signature freezes the default at module-import time — Python evaluates
default arguments once — which would make `monkeypatch.setattr(decide_module,
"DEFAULT_TIMEOUT_SECONDS", ...)` silently no-op in tests. `timeout_seconds: float | None = None` plus
an explicit `self._timeout_seconds = timeout_seconds if timeout_seconds is not None else
DEFAULT_TIMEOUT_SECONDS` in the body reads the global fresh on every construction, so
`tests/unit/test_agent_runtime.py::test_provider_timeout_falls_back_to_default_action` can shrink the
real 15s cap to something a unit test can afford, by monkeypatching before the seat is claimed.

**`create_app`/`RoomStore`/`Room` gained a `model_provider_factory` parameter, mirroring the existing
`adapters` injection point.** Lets tests substitute a mocked `agent_runtime.Provider` (never
touching the real OpenRouter API) the same way `adapters=` already substitutes `StubAdapter` for a
real game — dependency injection at the same seam, not a new pattern.
