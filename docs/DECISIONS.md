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