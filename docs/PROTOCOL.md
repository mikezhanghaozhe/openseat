# PROTOCOL.md

**Protocol version:** `0.1`
**Status:** frozen for M1. Verified against pokerkit 0.7.4 — see §10.
Changes go through this document first, then the code.

This document is the single source of truth. Every agent working on this repo reads this file
before writing code. If an implementation disagrees with this document, the document is right and
the implementation is a bug. If the document is wrong, change it here first, then fix the code.

---

## 0. Invariants

These are not negotiable and no package may violate them.

1. **The server is authoritative.** Clients send intent. The server decides what happened.
2. **`GameAdapter.view(state, seat)` is the only place redaction happens.** No endpoint, no
   WebSocket frame, and no log line may serialize game state by any other path.
3. **All randomness comes from the seeded RNG.** Same seed + same action sequence ⇒ byte-identical
   event log. No `random.random()`, no `time.time()` in game logic.
4. **No model output is trusted.** Every action from every seat — human, model, or agent — is
   validated against `legal_actions` before it touches state.
5. **`seq` is monotonic per room** and appears on every view and every event. It is the basis of
   reconnect, replay, and polling.
6. **Secrets never leave their scope.** BYOK API keys are held in memory for the room's lifetime,
   never persisted, never logged, never present in any response body.

---

## 1. Vocabulary

| Term | Meaning |
|---|---|
| **room** | A table. Holds config, seats, and a sequence of hands. Identified by `room_id`. |
| **seat** | A position at the table, `0..n-1`. Occupied by a human, model, or external agent. |
| **hand** | One deal from blinds to pot award. `hand_no` starts at 1. |
| **street** | `preflop` \| `flop` \| `turn` \| `river` |
| **seq** | Monotonic integer, starts at 0, increments once per event, scoped to the room. |

### Tokens

| Token | Scope | Grants | Shareable |
|---|---|---|---|
| `invite_token` | room | Claim an open seat | Yes — this is what goes in the share link |
| `seat_token` | seat | Read that seat's view, act for that seat | **No** — this is a secret |
| `host_token` | room | Start hands, change config, kick seats | No |

A `seat_token` is issued once when a seat is claimed and is the credential for every subsequent
call. Losing it means losing the seat; reconnect uses the same token.

Tokens are opaque capabilities — `secrets.token_urlsafe(32)`, held server-side, compared on every
request. There are no accounts. Holding the token *is* the authorization.

### Transport

Credentials go in headers, never in query strings. Query strings are written to proxy logs,
browser history, and `Referer` headers.

```
Authorization: Bearer sea_9dK...
```

Browsers cannot set headers on a WebSocket handshake. So the WS connection uses a **short-lived
ticket** instead:

```
POST /v1/rooms/{id}/ws-ticket   Authorization: Bearer sea_9dK...
  → { "ticket": "tkt_...", "expires_in": 30 }

wss://host/v1/rooms/{id}/ws?ticket=tkt_...
```

The ticket is single-use, expires in 30 seconds, and maps server-side to the seat. A leaked ticket
is worthless within half a minute; a leaked `seat_token` is not.

---

## 2. Notation

**Cards.** Two characters, rank then suit. Ranks `2 3 4 5 6 7 8 9 T J Q K A`. Suits `c d h s`.
Examples: `Ah`, `Td`, `2c`. A hidden card is `??`. Board is an ordered array: `["7c","7d","2s"]`.

**Chips.** Integers only. No floats anywhere in the money path — decide on the smallest unit
(1 chip) at room creation and stay in integers.

**Amounts are always "to", never "by".** This matches `complete_bet_or_raise_to` in PokerKit
(verified), so no conversion happens at the adapter boundary. This is the single most common
poker bug. A raise
specifies the **total amount this seat's bet on this street becomes**, not the increment.
If you have 100 committed and you raise to 300, you put in 200 more.

---

## 3. Actions

```jsonc
{ "type": "fold" }
{ "type": "check" }
{ "type": "call" }                    // amount is implied; server computes it
{ "type": "raise", "to": 300 }        // total for this street, see §2
{ "type": "show" }                    // showdown only, see §3.1
{ "type": "muck" }                    // showdown only, see §3.1
```

There is **no `all_in` action type.** Going all-in is `raise` with `to` equal to `max_raise_to`,
or `call` when your stack is less than `to_call`. The server marks the seat `all_in` and emits it
in the event.

`legal_actions` is computed server-side and returned with every view:

```jsonc
"legal_actions": [
  { "type": "fold" },
  { "type": "call", "amount": 100 },
  { "type": "raise", "min_to": 200, "max_to": 1300 }
]
```

- `check` and `call` are mutually exclusive — exactly one appears, never both.
- `fold` is omitted when `to_call == 0` (folding when you can check for free is not offered).
- `raise` is omitted when the seat is already all-in or `max_to <= min_to`.
- `max_to` is effectively the actor's stack, so all-in-for-less needs no special action type —
  it is a `raise` whose `min_to == max_to`.

### 3.1 Showdown is a turn phase, not an event

Showdown is not instantaneous. When at least one seat is *not* all-in, that seat may choose to
show or muck, and that choice is a real decision that crosses the wire.

- **All contested seats all-in** → no discretion exists. The server reveals every live hand and
  emits `showdown` immediately. `to_act` stays `null`.
- **At least one seat not all-in** → `phase` becomes `"showdown"`, `to_act` names the seat with
  the decision, and `legal_actions` is `[{"type":"show"},{"type":"muck"}]`.

The turn clock applies here too. On expiry the forced action is `muck` if the seat cannot win any
pot, otherwise `show` — never muck a winning hand on a timeout.

---

## 4. Observation

Returned by `GET /v1/rooms/{id}/view` and pushed as the WebSocket `state` frame. **Identical shape
in both.** This is what makes REST-first work.

```jsonc
{
  "protocol_version": "0.1",
  "seq": 47,
  "room_id": "r_8fk2",
  "hand_no": 3,
  "phase": "flop",
  "to_act": 2,
  "button": 0,

  "you": {
    "seat": 2,
    "name": "mike",
    "hole": ["Ah", "Kd"],
    "stack": 1300,
    "committed_street": 0,
    "committed_hand": 150,
    "status": "active"
  },

  "board": ["7c", "7d", "2s"],
  "pots": [
    { "index": 0, "amount": 450, "eligible_seats": [0, 2, 3] }
  ],
  "pot_total": 450,

  "seats": [
    { "seat": 0, "name": "GPT-5",  "kind": "model", "stack": 1200, "committed_street": 100,
      "status": "active", "last_action": { "type": "raise", "to": 100 } },
    { "seat": 1, "name": "alex",   "kind": "human", "stack": 950,  "committed_street": 0,
      "status": "folded", "last_action": { "type": "fold" } },
    { "seat": 2, "name": "mike",   "kind": "human", "stack": 1300, "committed_street": 0,
      "status": "active", "last_action": null },
    { "seat": 3, "name": "Claude", "kind": "agent", "stack": 1550, "committed_street": 100,
      "status": "active", "last_action": { "type": "call" } }
  ],

  "to_call": 100,
  "min_raise_to": 200,
  "max_raise_to": 1300,
  "legal_actions": [
    { "type": "fold" },
    { "type": "call", "amount": 100 },
    { "type": "raise", "min_to": 200, "max_to": 1300 }
  ],

  "chat": [
    { "seq": 44, "seat": 0, "name": "GPT-5", "text": "I've got a read on you." }
  ],

  "text": "You are Seat 2 (BTN) with Ah Kd.\nBoard: 7c 7d 2s\nPot: 450 | To call: 100\nSeat 0 (GPT-5) raised to 100. Seat 1 folded. Seat 3 called.\nLegal: fold, call 100, raise 200-1300."
}
```

**Seat status:** `active` | `folded` | `all_in` | `sitting_out` | `busted`

**Redaction contract, stated precisely:** `seats[i].hole` does not exist as a field for `i != you.seat`.
It is absent, not `null`, not `["??","??"]` — absent. The only exception is after a `showdown`
event, where revealed hands appear in the event payload and in `seats[i].revealed`.

`text` is a rendering of the same data for LLM seats. It must never contain information absent from
the structured fields — the renderer takes the redacted observation as input, not raw state.

---

## 5. Events

Public, broadcast to every connection in the room including spectators. **An event never contains
hidden information.** Every event has `{ seq, type, ts, ...payload }`.

| `type` | Payload |
|---|---|
| `room_created` | `{ game, config, seats_total }` — **no seed**, see §10 |
| `seat_joined` | `{ seat, name, kind }` |
| `seat_left` | `{ seat, reason }` |
| `hand_started` | `{ hand_no, button, stacks: [...] }` |
| `blinds_posted` | `{ postings: [{ seat, amount, kind: "sb"\|"bb"\|"ante" }] }` |
| `hole_cards_dealt` | `{ seats: [0,2,3] }` — who received cards, never what |
| `action_required` | `{ seat, deadline_ms }` |
| `action_taken` | `{ seat, action, amount_added, stack_after, pot_after, all_in }` |
| `board_dealt` | `{ street: "flop", cards: ["7c","7d","2s"] }` |
| `table_talk` | `{ seat, name, text }` |
| `showdown` | `{ reveals: [{ seat, hole, rank_class, description }] }` |
| `pot_awarded` | `{ pots: [{ index, amount, winners: [seat], reason }] }` |
| `hand_complete` | `{ hand_no, stacks: [...], hand_seed }` — seed disclosed only here, see §10 |
| `seat_timed_out` | `{ seat, forced_action }` |
| `room_complete` | `{ final_stacks, ranking }` |

The event log **is** the transcript. Persist it as JSONL, one event per line. Replay = re-read it.

---

## 6. REST endpoints (M1)

Base path `/v1`. All bodies JSON. All responses include `protocol_version`.

### `POST /rooms`
```jsonc
// request
{ "game": "holdem-nl", "seats": 4,
  "config": { "sb": 25, "bb": 50, "ante": 0, "starting_stack": 5000, "turn_seconds": 30 },
  "seed": 42 }
// 201
{ "room_id": "r_8fk2", "invite_token": "inv_...", "host_token": "hst_...",
  "seats": [{ "index": 0, "status": "open" }, ...] }
```
`seed` is accepted **only** when the server runs with `ARENA_ALLOW_FIXED_SEED=1` (tests only —
see §10). Otherwise the server draws its own and never discloses it during play. Per-hand seeds
appear in `hand_complete` after the hand is over.

### `POST /rooms/{id}/seats`
```jsonc
// request
{ "invite_token": "inv_...", "seat": 2, "kind": "human", "display_name": "mike" }
// 201
{ "seat_token": "sea_...", "seat_index": 2 }
```
`kind`: `human` | `model` | `agent`. For `model`, see M3 fields (`model`, `key_mode`, `api_key`).
`seat` may be omitted to take the lowest open seat.

### `GET /rooms/{id}`
Public room summary. Never requires a token. Never contains hole cards, stacks-in-hand detail
beyond what's already public, or any token.

### `POST /rooms/{id}/start`
`{ "host_token": "hst_..." }` → `{ "hand_no": 1, "to_act": 0, "seq": 12 }`

### `GET /rooms/{id}/view`
`Authorization: Bearer <seat_token>`
→ Observation (§4). This is a pure read; calling it never advances state.

### `POST /rooms/{id}/actions`
```jsonc
// request
{ "seat_token": "sea_...", "request_id": "uuid-v4",
  "action": { "type": "raise", "to": 300 }, "table_talk": "your move" }
// 200
{ "seq": 48, "accepted": true }
// 409
{ "error": "illegal_action", "reason": "raise below min_raise_to",
  "legal_actions": [...] }
```

**Idempotency:** `request_id` is required. A repeat of a `request_id` already applied returns the
*original* result with `"replayed": true` and does not re-apply. This is what makes network retries
safe — without it a dropped response turns into a double raise.

**On 409, the turn pointer does not move.** The seat may try again.

### `GET /rooms/{id}/events?since=0`
→ `{ "events": [...], "latest_seq": 48 }`. Public. Also the replay source.

### `GET /rooms/{id}/result`
→ `{ "hand_no", "winners", "pots", "final_stacks", "showdown" }`

---

## 7. Error codes

| HTTP | `error` | Meaning |
|---|---|---|
| 400 | `bad_request` | Malformed body or unknown action type |
| 401 | `invalid_token` | Token missing, malformed, or not for this room |
| 403 | `not_your_turn` | Valid seat, wrong turn |
| 404 | `room_not_found` | |
| 409 | `illegal_action` | Well-formed but not in `legal_actions`. Includes `legal_actions`. |
| 409 | `seat_taken` | |
| 409 | `room_full` | |
| 410 | `room_closed` | Room finished or expired |
| 429 | `rate_limited` | |

Every error body: `{ "error": "...", "reason": "human readable", ...context }`.

---

## 8. WebSocket (M2)

`wss://host/v1/rooms/{id}/ws?ticket=...` — ticket obtained from `POST /rooms/{id}/ws-ticket`, see §1.
Spectators obtain a spectator ticket with `invite_token` and receive `event` frames only — **never `state`.**

**Server → client**
```jsonc
{ "t": "hello", "seq": 47, "seat": 2, "replay": [ ...events from 0... ] }
{ "t": "state", "payload": { ...Observation, identical to GET /view... } }
{ "t": "event", "payload": { seq, type, ... } }
{ "t": "clock", "seat": 2, "deadline_ms": 28400 }
{ "t": "error", "code": "illegal_action", "reason": "...", "legal_actions": [...] }
{ "t": "pong" }
```

**Client → server**
```jsonc
{ "t": "act", "request_id": "uuid", "action": {...}, "table_talk": "..." }
{ "t": "resume", "since": 47 }
{ "t": "ping" }
```

**Reconnect:** reopen with the same `seat_token`, send `resume` with your last `seq`. Server replays
events `since+1..latest` then sends current `state`. Clients must tolerate duplicate events and
dedupe on `seq`.

**Turn clock:** the server starts a timer on `action_required`. On expiry it applies the forced
action — `check` if legal, otherwise `fold` — and emits `seat_timed_out`. The clock is server-side
and authoritative; the client's countdown is decoration.

**Heartbeat:** client pings every 20s. Server closes a socket silent for 60s. A closed socket does
not vacate the seat; the seat is held until the room ends or the host kicks it.

---

## 9. GameAdapter

Poker is game one, not the product. Every game implements this and nothing outside
`packages/game-*` may know poker exists.

```python
class GameAdapter(Protocol):
    id: str                        # "holdem-nl"
    min_players: int
    max_players: int
    config_schema: dict            # JSON Schema; rendered as the room-creation form

    def reset(self, cfg: dict, seed: int) -> GameState: ...
    def legal_actions(self, s: GameState, seat: int) -> list[ActionSpec]: ...
    def apply(self, s: GameState, seat: int, a: Action) -> list[Event]: ...
    def view(self, s: GameState, seat: int) -> Observation: ...   # ONLY redaction point
    def is_terminal(self, s: GameState) -> bool: ...
    def results(self, s: GameState) -> dict[int, float]: ...
```

`apply` returns events rather than mutating visibly — the room server assigns `seq` and broadcasts.
`apply` must raise `IllegalAction` rather than silently correcting anything.

---

## 10. PokerKit adapter notes

Verified against **pokerkit 0.7.4**. These are binding on `packages/game-holdem` and on nothing
else — no other package may reference PokerKit types or semantics.

### Derivations

Several protocol fields do not exist in PokerKit and must be computed. This table is the contract:

| Protocol field | Derived from |
|---|---|
| `committed_street` | `state.bets[i]` |
| `committed_hand` | `state.starting_stacks[i] - state.stacks[i]` — **only valid mid-hand.** Once chips are pushed at hand end this goes negative for winners. Snapshot it before award, or gate the field on `phase != "hand_complete"`. |
| `status: "folded"` | `not state.statuses[i]` |
| `status: "all_in"` | `state.statuses[i] and state.stacks[i] == 0` |
| `pots[]` | `state.pots` → `Pot(amount, player_indices)`, already split per stack tier |
| `min_raise_to` / `max_raise_to` | Live properties; re-read after **every** action, never cache |

**Do not use `state.folded_status` or `state.all_in_status`.** They are last-operation flags, not
per-seat arrays. The per-seat list is `state.statuses`. This is the highest-risk trap in the
adapter — a leaked misuse looks like a rules bug and is very hard to trace.

### Card dealing

`deal_hole` and `deal_board` take **one concatenated string per call** — `"AsKd"`, not
`["As","Kd"]`. Passing a list silently stores raw `str` instead of `Card` objects and crashes
later inside hand evaluation, far from the cause.

Confine this to a single `_deal()` helper and assert on the way out:

```python
assert all(isinstance(c, Card) for c in state.hole_cards[i]), "hole cards not parsed"
```

That assertion is cheap and turns a delayed mystery crash into an immediate one.

### Seeding — the seed is a secret during a hand

Each hand is dealt from a deck shuffled by a seeded RNG. **This does not make cards predictable:**
the per-hand seed is drawn from `secrets.randbits(64)`, so every hand is unpredictable. What the
seed buys is reproducibility — a transcript can be replayed exactly, a bug can be reproduced from
a saved hand, and the same deck can be re-run against a different model.

**But an exposed seed is an exposed deck.** Anyone holding the seed and knowing the shuffle can
compute every hole card at the table. Therefore:

| | During the hand | After `hand_complete` |
|---|---|---|
| `hand_seed` | Server memory + server-side transcript only. **Never** in a view, event, or response body. | Included in the `hand_complete` event |

- The room holds a `master_seed`; each hand's seed is `derive(master_seed, hand_no)`.
- `master_seed` is **never** transmitted to any client, ever — not even after the room ends.
  Revealing it would expose every future hand in that room.
- `room_created` does **not** carry a seed. (Earlier drafts of this document did. That was a bug.)
- An explicit `seed` may be passed to `POST /rooms` **only when the server runs with
  `ARENA_ALLOW_FIXED_SEED=1`**, which is off in production. This exists for tests and nothing else.

### Contract tests this implies

- [ ] No seed value appears in any view, event, or error body before `hand_complete`
- [ ] `master_seed` never appears in any client-facing payload at any time
- [ ] `POST /rooms` with an explicit seed is rejected when the fixed-seed flag is off

### Automation

Proposed flags confirmed workable: automate ante posting, blind posting, bet collection, hand
killing, and chip pushing/pulling. Keep `HOLE_CARDS_SHOWING_OR_MUCKING` manual.

Caveat: when every contested seat is all-in there is no discretion left and showdown resolves
automatically regardless of that flag. The manual path only engages when a non-all-in seat could
choose — there `actor_index` is `None` and you drive `show_or_muck_hole_cards(show, player_index)`
in whatever order you like via `showdown_indices`. See §3.1 for how this surfaces on the wire.

### Contract tests this implies

- [ ] Dealt hole cards are `Card` instances, not `str`
- [ ] `raise to N` moves the actor's stack by `N - bets[i]`, not by `N`
- [ ] 3-way all-in with unequal stacks produces >1 entry in `pots[]` with correct `eligible_seats`
- [ ] Seat status matches the derivation table across fold / all-in / active
- [ ] `committed_hand` is not read after chips are pushed
- [ ] Showdown with one non-all-in seat exposes `show`/`muck` in `legal_actions`