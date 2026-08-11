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