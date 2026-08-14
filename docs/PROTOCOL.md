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
3. **All randomness comes from the seeded RNG.** Same seed + same action sequence ⇒ identical
   event log **excluding `ts`**. Wall-clock timestamps necessarily differ between runs; determinism
   comparisons strip `ts` and compare on `seq` ordering plus payloads. No `random.random()` and no
   `time.time()` anywhere in game logic — `ts` is stamped by the room server at emit time, outside
   the adapter.
4. **No model output is trusted.** Every action from every seat — human, model, or agent — is
   validated against `legal_actions` before it touches state.
5. **`seq` is monotonic per room** and appears on every view and every event. It is the basis of
   reconnect, replay, and polling.
6. **Actions for a room are applied one at a time, in receipt order.** Validating `to_act` and
   applying the action must be atomic per room — a race where two requests both read `to_act`
   before either commits breaks everything downstream. One writer per room. If `room-server` is
   ever scaled horizontally, shard by `room_id`; do not share rooms across workers.
7. **Secrets never leave their scope.** BYOK API keys are held in memory for the room's lifetime,
   never persisted, never logged, never present in any response body.

---

## 0.1 M1 scope

**An M1 room plays exactly one hand.** After `hand_complete` the room is finished; there is no
next-hand trigger, no busted-seat bookkeeping, and no `room_complete` condition to define. Seats
are all claimed before `/start` and the set does not change.

Multi-hand rooms arrive in M2 and bring three questions with them, deliberately deferred rather
than guessed at now: what advances to the next hand, when a room ends, and how the button and
blinds rotate around seats with no chips. Do not design for them here.

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
| `host_token` | room | Start the hand. (Config change and kick arrive in M2 — no endpoint exists in M1.) | No |

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

**Amounts are always "to", never "by".** A raise specifies the **total amount this seat's bet on
this street becomes**, not the increment — if you have 100 committed and raise to 300, you put in
200 more. This matches `complete_bet_or_raise_to` in PokerKit (verified), so no conversion happens
at the adapter boundary. It is also the single most common poker bug.

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
- `raise` is included whenever **both bounds are non-`None` and `max_to >= min_to`.** Equality is
  legal and must be offered — it is the short-stack all-in raise.
- **Check for `None` before comparing.** PokerKit returns `None` — not a small number — when raising
  is not legal for the actor, and `None <= None` raises `TypeError`.
- `max_raise_to` is **`stacks[i] + bets[i]`**, the actor's total street capacity, not the remaining
  stack alone. All-in-for-less needs no special action type: it is a `raise` with `min_to == max_to`.

### 3.0 Most hands never reach showdown

When every seat but one folds, the last seat standing wins without revealing anything. There is
**no `showdown` event** in this case — the log goes straight from the winning fold's `action_taken`
to `pot_awarded` with `reason: "uncontested"`. This is the most common way a hand ends, not an edge
case. Derived from `state.can_win_now` / `state.win_now` (§10).

### 3.1 Showdown is a turn phase, not an event

Showdown is not instantaneous. When at least one seat is *not* all-in, that seat may choose to
show or muck, and that choice is a real decision that crosses the wire.

- **All contested seats all-in** → no discretion exists. The server reveals every live hand and
  emits `showdown` immediately. `to_act` stays `null`.
- **At least one seat not all-in** → `phase` becomes `"showdown"`, `to_act` names the seat with
  the decision, and `legal_actions` is `[{"type":"show"},{"type":"muck"}]`.

**Order is PokerKit's `showdown_indices`, followed exactly.** Do not invent an order — reveals
become visible as they happen and change what later seats know, so a different order is a different
game. Each choice emits its own `showdown` event immediately; there is no aggregate-only reveal, so
a client reconnecting mid-showdown can reconstruct the partial state from the log.

**A muck forfeits pot eligibility**, even for a hand that would have won. See §3.2.

### 3.2 Showdown timeout

The turn clock applies here (M2 onward). On expiry the forced action is `muck` only if the seat
cannot win any pot, otherwise `show` — never muck a winning hand on a timeout, since a disconnect
must not cost someone a pot they had won.

**`state.can_win_now(seat)` is the right primitive and means less than it sounds.** It asks whether
the hand can beat the hands *already exposed*, not an omniscient comparison against every hidden
hand. That is exactly what is wanted here — it matches what a dealer could determine at that moment
— but do not read it as "this seat is the winner."

Mucking forfeits eligibility for every pot, which is why the default leans toward showing.

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

**Phase:** `waiting` | `preflop` | `flop` | `turn` | `river` | `showdown` | `hand_complete`.
`waiting` covers the window between room creation and `/start`. `street` (§1) is the betting-round
subset — `preflop` | `flop` | `turn` | `river` — and is what `board_dealt.street` carries, minus
`preflop`, which deals no board.

**Seat status:** `active` | `folded` | `all_in` — these are the only values M1 emits.
`sitting_out` and `busted` are **reserved for M2** and must never appear in an M1 payload; likewise
the `room_complete` and `seat_left` events. §0.1 scopes M1 to a single hand, so no seat outlives
its hand.

**Redaction contract, stated precisely:** `seats[i].hole` does not exist as a field for `i != you.seat`.
It is absent, not `null`, not `["??","??"]` — absent. The only exception is after a `showdown`
event, where revealed hands appear in the event payload and in `seats[i].revealed`.

`text` is a rendering of the same data for LLM seats. It must never contain information absent from
the structured fields — the renderer takes the redacted observation as input, not raw state.

---

## 5. Events

Public, broadcast to every connection in the room including spectators. **An event never discloses
hidden information the server derived.**

A seat voluntarily revealing its *own* cards through `table_talk` ("I have Ah Kd") is legal poker —
speech play is part of the game, and truthfulness is not required. The invariant constrains the
*server*, not the players. Never attempt to filter chat for card tokens: it is bypassable, produces
false positives, and would make a secrecy boundary out of something that is not one. Every event has `{ seq, type, ts, ...payload }`.

| `type` | Payload |
|---|---|
| `room_created` | `{ game, config, seats_total }` — **no seed**, see §10 |
| `seat_joined` | `{ seat, name, kind }` |
| `seat_left` | `{ seat, reason }` — **M2 reserved** |
| `hand_started` | `{ hand_no, button, stacks: [...] }` |
| `blinds_posted` | `{ postings: [{ seat, amount, kind: "sb"\|"bb"\|"ante" }] }` |
| `hole_cards_dealt` | `{ seats: [0,2,3] }` — who received cards, never what |
| `action_required` | `{ seat, deadline_ms }` |
| `action_taken` | `{ seat, action, amount_added, stack_after, pot_after, all_in }` |
| `board_dealt` | `{ street: "flop", cards: ["7c","7d","2s"] }` |
| `table_talk` | `{ seat, name, text }` |
| `showdown` | `{ reveals: [{ seat, hole, rank_class, description }] }` — one entry per discretionary `show`; all live hands at once in the all-in case (§3.1) |
| `pot_awarded` | `{ pots: [{ index, amount, awards: [{seat, amount}], reason }] }` — `reason` is `"uncontested"` \| `"showdown"`. **`awards` is authoritative**, not a winner list: a split with an odd chip gives winners unequal amounts, which `winners: [seat]` cannot express. Sum of `awards[].amount` equals the pot `amount` exactly. |
| `hand_complete` | `{ hand_no, stacks: [...], deck }` — the hand's full 52-card shuffle, disclosed only here, see §10 |
| `seat_timed_out` | `{ seat, forced_action }` — **`forced_action` is omitted for showdown-phase timeouts.** See §5.1. |
| `room_complete` | `{ final_stacks, ranking }` — **M2 reserved** |

### 5.0 Canonical event order

Determinism is untestable without a fixed order. These sequences are required.

**Setup:** `room_created` → `seat_joined` (one per seat, in seat index order) → `hand_started` →
`blinds_posted` → `hole_cards_dealt` → `action_required`.

**Each action** emits, atomically, in this order:
`table_talk` (if present) → `action_taken` → then whichever terminal suffix applies:

| Situation | Suffix |
|---|---|
| Betting continues on this street | `action_required` |
| Street ends, more to come | `board_dealt` → `action_required` |
| All but one seat folded | `pot_awarded` → `hand_complete` |
| River betting ends, showdown needed | `action_required` (showdown turn) or, if no discretion, `showdown` → `pot_awarded` → `hand_complete` |
| A showdown `show`/`muck` with seats still to decide | `showdown` (that seat's reveal) → `action_required` |
| The last showdown decision | `showdown` → `pot_awarded` → `hand_complete` |

Every event in one action's suffix shares that action's atomic application — a reader never sees a
partial transition.

**Two stores, and only one is public.**

| Store | Contains | Served by |
|---|---|---|
| Public event log (JSONL) | Events exactly as broadcast | `GET /events`, WS `event` frames |
| Private hand record | `room_seed`, the in-progress deck | **nothing** — server-side only |

"The event log is the transcript" refers to the **public** log. The private hand record is not a
transcript and is never served. Its per-hand seed is copied into the public `hand_complete` event
when the hand ends; `room_seed` never is.

### 5.1 Why `forced_action` is withheld at showdown

A forced `show`/`muck` is computed from whether the seat can win — from hidden information — so
broadcasting it proves the hand was beaten before a card is revealed. Non-showdown timeouts force
`check` or `fold`, which are legality facts already public via `legal_actions`, so those are safe.

**The general rule:** invariant 2 protects *serialized state*. It does nothing about *derived
values* in public events. Before adding a field to any event, ask what hidden state its value is
computed from.

---

## 6. REST endpoints (M1)

Base path `/v1`. All bodies JSON. All responses include `protocol_version`.

### `POST /rooms`
```jsonc
// request
{ "game": "holdem-nl", "seats": 4,
  "config": { "sb": 25, "bb": 50, "ante": 0, "starting_stack": 5000, "turn_seconds": 30 },
  // or: "starting_stacks": [5000, 1200, 300, 5000] — see below
  "seed": 42 }
// 201
{ "room_id": "r_8fk2", "invite_token": "inv_...", "host_token": "hst_...",
  "seats": [{ "index": 0, "status": "open" }, ...] }
```
**Stacks may be unequal.** `config.starting_stack` sets every seat's stack. `config.starting_stacks`
(a list, one entry per seat) overrides it per seat. Exactly one of the two must be present.

```jsonc
"config": { "sb": 25, "bb": 50, "starting_stacks": [5000, 1200, 300, 5000] }
```

This is a normal table state, not a test facility — real tables have unequal stacks as soon as
anyone doubles up or rebuys short. It exists in M1 because a single hand from a fresh room cannot
otherwise reach one: with equal stacks, every live seat has contributed the same amount at every
point, so tiered all-ins and side pots are mathematically unreachable. Without it the side-pot path
— the highest-risk code in the engine — could not be tested at all.

Validation: length equals `seats`; every entry is an integer `>= bb`; both fields present is
`400 invalid_config`.

**Config is validated server-side before anything is created.** `config` is checked against the
adapter's `config_schema` and `seats` against `min_players`/`max_players`; violations return
`400 invalid_config` naming the specific constraint. `config_schema` defines what is *legal*, not
merely what a form renders — invariant 4 applies to room creation exactly as it applies to actions.
A bad config must fail here, not crash inside `reset()` far from the cause.

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
`{ "host_token": "hst_..." }` → `{ "hand_no": 1, "to_act": 0, "first_seq": 0, "last_seq": 12 }`

**Every declared seat must be occupied.** Starting with any seat still `open` returns
`409 seats_not_filled`. M1 does not support joining a running room.

**Idempotent by nature:** calling `/start` on an already-started room returns the current values
unchanged rather than dealing again. No `request_id` needed.

### `GET /rooms/{id}/view`
`Authorization: Bearer <seat_token>`
→ Observation (§4). This is a pure read; calling it never advances state.

**Requires a `seat_token` specifically.** A missing token, an `invite_token`, a `host_token`, or a
`seat_token` issued for a different room all return `401 invalid_token`. There is no default seat
and no fallback rendering — if the token does not resolve to exactly one seat in this room, the
request fails.

**There is no REST spectator observation endpoint.** Spectators get `GET /rooms/{id}` and
`GET /rooms/{id}/events` only. Never widen this endpoint to accept a weaker credential.

### `POST /rooms/{id}/actions`
```jsonc
// request
{ "seat_token": "sea_...", "request_id": "uuid-v4",
  "action": { "type": "raise", "to": 300 }, "table_talk": "your move" }
// 200
{ "first_seq": 48, "last_seq": 51, "accepted": true }
// 409
{ "error": "illegal_action", "reason": "raise below min_raise_to",
  "legal_actions": [...] }
```

**Idempotency:** `request_id` is required. A repeat of a `request_id` already applied returns the
*original* result with `"replayed": true` and does not re-apply. This is what makes network retries
safe — without it a dropped response turns into a double raise.

If a repeated `request_id` arrives with a **different** `action` body, return
`409 request_id_conflict`. Silently replaying the first action while the caller believes the second
took effect is worse than an error. A genuinely different action needs a fresh `request_id`.

**On 409, the turn pointer does not move.** The seat may try again.

**`first_seq` / `last_seq` bound the events this request caused.** One action commonly emits
several — `action_taken`, then possibly `board_dealt`, `action_required`, `showdown`,
`pot_awarded`, `hand_complete`. A polling client passes `since=last_seq` to `/events` and knows it
has seen every consequence of its own request. `/start` returns the same pair.

**Idempotency identity is `(room_id, seat, request_id)`.** Comparison is over the canonical
`action` only — `table_talk` is excluded, so retrying with different chat does not conflict. An id
is reserved only when an action **commits**; malformed, unauthorized, wrong-turn, and illegal
requests reserve nothing and may reuse the id. Check-and-store happens inside the room's atomic
section (invariant 6).

### `GET /rooms/{id}/events?since=0`
→ `{ "events": [...], "latest_seq": 48 }`. Public. Also the replay source.

### `GET /rooms/{id}/result`
→ `{ "hand_no", "pots", "final_stacks", "showdown" }`. Public; no token required, since every field
is a projection of public events. Returns `409 hand_in_progress` before `hand_complete`.

**Reads never 410.** `GET /rooms/{id}`, `/view`, `/events`, and `/result` stay readable after the
room closes — that is what "the event log is the transcript" promises, and the M1 gate reads
`/result` immediately after the room's only hand completes. `410 room_closed` applies to `/start`
and `/actions` only.

`/result` is a **mechanical projection of the `pot_awarded`, `showdown`, and `hand_complete`
events, replayed from the log** — never a fresh serialization of `GameState`. Mucked and folded
hole cards were never in those events and must not appear here. This endpoint is a plausible place
to reach for `state.hole_cards`; don't.

---

## 7. Error codes

| HTTP | `error` | Meaning |
|---|---|---|
| 400 | `bad_request` | Malformed body or unknown action type |
| 400 | `invalid_config` | Config failed adapter validation; body names the violated constraint |
| 401 | `invalid_token` | Token missing, malformed, or not for this room |
| 403 | `not_your_turn` | Valid seat, wrong turn |
| 404 | `room_not_found` | |
| 409 | `illegal_action` | Well-formed but not in `legal_actions`. Includes `legal_actions`. |
| 409 | `request_id_conflict` | Same `request_id`, different action body |
| 409 | `seats_not_filled` | `/start` called with seats still open |
| 409 | `seat_taken` | |
| 409 | `room_full` | |
| 409 | `hand_in_progress` | `/result` called before `hand_complete` |
| 410 | `room_closed` | **Mutating endpoints only** (`/start`, `/actions`). Reads never 410 — see §6. |
| 429 | `rate_limited` | |

Every error body: `{ "error": "...", "reason": "human readable", ...context }`.

**`reason` and any context must be built only from the request itself and the caller's own redacted
observation.** Never from raw `GameState`. An error message is a serialization path like any other,
and it is the easiest one to leak through by accident.

**Receipt order** (invariant 6) means insertion into a single per-room ordered queue. Requests are
processed one at a time from that queue; a request for a seat that is not `to_act` when it reaches
the front gets `403 not_your_turn`.

---

## 8. WebSocket (M2)

`wss://host/v1/rooms/{id}/ws?ticket=...` — ticket obtained from `POST /rooms/{id}/ws-ticket`, see §1.
Spectators obtain a spectator ticket with `invite_token` and receive `event` frames only — **never `state`.**

**Server → client**
```jsonc
{ "t": "hello", "seq": 47, "seat": 2, "replay": [ ...events from since+1, or from 0 on first connect... ] }
{ "t": "state", "payload": { ...Observation, identical to GET /view... } }
{ "t": "event", "payload": { seq, type, ... } }
{ "t": "clock", "seat": 2, "deadline_ms": 1786742400000 }   // absolute server epoch ms, not a duration
{ "t": "error", "code": "illegal_action", "reason": "...", "legal_actions": [...] }
{ "t": "pong" }
```

**Client → server**
```jsonc
{ "t": "act", "request_id": "uuid", "action": {...}, "table_talk": "..." }
{ "t": "resume", "since": 47 }
{ "t": "ping" }
```

**Reconnect:** get a fresh ws-ticket, reopen, send `resume` with your last `seq`. The server
captures `latest_seq` once, replays `since+1..latest_seq`, then sends the `state` **as of exactly
that sequence** — one atomic snapshot, so a client never acts on a view that predates events it has
already replayed. Clients must tolerate duplicates and dedupe on `seq`.

**A timeout wins any race.** Once a deadline has passed at the server, the forced action applies
even if a reconnect or a real action arrives concurrently. Deadlines are absolute server epoch
milliseconds — never durations, which are unusable across a reconnect.

**Turn clock (M2 onward).** `turn_seconds` is accepted at room creation in M1 but **not enforced**
until this milestone. A background timer firing during M1's REST contract tests would make them
nondeterministic, which fights invariant 3. In M1 a hand waits indefinitely for `POST /actions`.

The server starts a timer on `action_required`. On expiry it applies the forced
action — `check` if legal, otherwise `fold` — and emits `seat_timed_out`. The clock is server-side
and authoritative; the client's countdown is decoration.

**Heartbeat:** client pings every 20s. Server closes a socket silent for 60s. A closed socket does
not vacate the seat; the seat is held until the room ends. (Host kick is M2.)

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

    def reset(self, cfg: dict, deck: list[str]) -> GameState: ...
        # The room server draws the shuffle and hands the adapter a full 52-card deck.
        # The adapter does no shuffling and knows nothing about seeding.
    def legal_actions(self, s: GameState, seat: int) -> list[ActionSpec]: ...
    def apply(self, s: GameState, seat: int, a: Action) -> list[Event]: ...
    def view(self, s: GameState, seat: int) -> Observation: ...   # ONLY redaction point
    def waiting_view(self, cfg: dict, seats: list[SeatJoinedPayload], seat: int) -> Observation: ...
        # Builds the pre-/start Observation (no GameState exists yet). `seats` is every
        # currently-claimed seat's join-time metadata; `seat` is the requesting seat's index.
        # Same envelope-overlay contract as view(): protocol_version/seq/room_id/chat are
        # placeholders the room server overlays afterward.
    def is_terminal(self, s: GameState) -> bool: ...
    def results(self, s: GameState) -> dict[int, float]: ...
        # float because GameAdapter is game-agnostic (a future game may score 0.5 for a draw).
        # game-holdem returns an INTEGER chip delta (stack_after - starting_stack) in that float.
        # Never do further arithmetic on it, never round it, never treat it as money — render only.
        # The money path stays integer end to end; this is a reporting value.
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
| `committed_hand` | `state.starting_stacks[i] - state.stacks[i]` — **valid mid-hand only.** Goes negative for winners once chips are pushed, and there is no defined instant before automated pushing, so snapshot it yourself at each street end. Omit the field entirely when `phase == "hand_complete"`. |
| `status: "folded"` | `not state.statuses[i]` — **but `statuses` means "inactive", which also covers showdown hand-killing.** It is not a durable folded/busted classification. Maintain our own per-seat status alongside PokerKit state rather than deriving it fresh each view. |
| `status: "all_in"` | `state.statuses[i] and state.stacks[i] == 0` |
| `to_call` | `state.checking_or_calling_amount` — **`int \| None`.** `None` when there is no actor (showdown, terminal). Omit the field entirely rather than serializing `null`; §4's integer shape assumes an actor exists. |
| `pots[]` | `state.pots`, already split per stack tier. The real dataclass is `Pot(raked_amount, unraked_amount, player_indices)` — `.amount` is a derived property, **not** a constructor argument. Matters when building test fixtures. |
| uncontested win | `state.can_win_now(seat)` / `state.win_now` — the walk-the-pot path, §3.0. `can_win_now` compares against **already-exposed** hands only, not every hidden hand. See §3.2. |
| `min_raise_to` / `max_raise_to` | `state.min_/max_completion_betting_or_raising_to_amount`. **`int \| None`** — `None` means raising is not legal for this actor. Re-read after every action, never cache. See §3. |

**Do not use `state.folded_status` or `state.all_in_status`.** They are last-operation flags, not
per-seat arrays. The per-seat list is `state.statuses`. This is the highest-risk trap in the
adapter — a leaked misuse looks like a rules bug and is very hard to trace.

### `state.pots` is an iterator, not a list

Materialize it **once** per view or event construction:

```python
pots = tuple(state.pots)   # exhausting it twice yields inconsistent pots and pot_total
```

Deriving `pots[]` and `pot_total` from two separate reads of `state.pots` is a real bug, not a
style preference.

### Forced-off features

PokerKit supports two things this protocol has no concept of. Both must be disabled in M1, and
neither may be enabled without a protocol change.

**Rake.** `Pot.raked_amount` must always be `0`, so `pot.amount == pot.unraked_amount` holds. There
is no rake field in room config and the wire format has nowhere to report it.

**Run it twice.** `select_runout_count` / `runout_count` let an all-in pot be run out multiple
times with the pot split across runouts — which produces fractional chips and breaks the
integers-only money path. Force `runout_count` to 1. The wire protocol has no event shape for
multiple boards and no action for a seat to request one.

### Card dealing

`deal_hole` and `deal_board` take **one concatenated string per call** — `"AsKd"`, not
`["As","Kd"]`. Passing a list silently stores raw `str` instead of `Card` objects and crashes
later inside hand evaluation, far from the cause.

Confine this to a single `_deal()` helper and assert on the way out:

```python
assert all(isinstance(c, Card) for c in state.hole_cards[i]), "hole cards not parsed"
```

That assertion is cheap and turns a delayed mystery crash into an immediate one.

### Dealing — one shuffle per hand, and the deck is what gets published

The room holds a single `room_seed` (`secrets.randbits(64)`). At the **start of each hand** the
room server draws one complete 52-card shuffle from it and passes that deck to
`GameAdapter.reset(cfg, deck)`. The adapter never shuffles and never sees a seed.

**Draw a whole shuffle per hand, never card-by-card on demand.** Lazy drawing makes the number of
RNG calls depend on how the hand played — a hand ending preflop consumes fewer cards than one
reaching the river — so hand 2's cards would depend on how hand 1 *went*, not just on the seed.
That breaks the "same deck, different model" comparison outright. A constant number of draws per
hand keeps hand N's deck fixed regardless of what happened before it.

**The deck is the replay artifact, not the seed.** A seed only reproduces a deck if the identical
shuffle code runs — same algorithm, same RNG, same Python version. Refactor the shuffle and every
stored seed silently replays a *different* hand. A stored deck needs no algorithm; it is the
answer. 52 strings per hand is not worth optimising away.

| | During the hand | After `hand_complete` |
|---|---|---|
| The hand's deck | Private hand record only. **Never** in a view, event, or response body — an exposed deck is every hole card at the table. | Published in the `hand_complete` event |
| `room_seed` | Server memory only | **Never published.** It generates every hand in the room, including ones not yet dealt. |

- `room_created` does **not** carry a seed or a deck.
- An explicit `seed` may be passed to `POST /rooms` **only when the server runs with
  `ARENA_ALLOW_FIXED_SEED=1`**, off in production. This exists for reproducible tests
  (invariant 3) and nothing else — it is not the replay mechanism.

- [ ] No deck or card appears in any view, event, or error body before `hand_complete`
- [ ] `room_seed` never appears in any client-facing payload at any time
- [ ] An explicit seed is rejected unless the fixed-seed flag is on
- [ ] A hand ending preflop consumes the same number of RNG draws as one reaching the river
- [ ] Dealt hole cards are `Card` instances, not `str`
- [ ] `raise to N` moves the actor's stack by `N - bets[i]`, not by `N`
- [ ] 3-way all-in with unequal stacks produces >1 entry in `pots[]` with correct `eligible_seats`
- [ ] Seat status matches the derivation table across fold / all-in / active
- [ ] `committed_hand` is not read after chips are pushed
- [ ] Showdown with one non-all-in seat exposes `show`/`muck` in `legal_actions`
- [ ] A seat with no legal raise (bounds are `None`) yields `legal_actions` without `raise`, and
      does not raise `TypeError`
- [ ] Everyone folding to one seat emits `pot_awarded` with `reason: "uncontested"` and **no**
      `showdown` event
- [ ] `GET /view` returns 401 for a missing token, an `invite_token`, a `host_token`, and a
      `seat_token` from another room — and never renders a default seat
- [ ] `text` for seat *i* contains no card token belonging to any other live, un-mucked seat, at
      every phase
- [ ] `GET /result` contains no hole card absent from the room's public event log
- [ ] `seat_timed_out` for a showdown-phase timeout omits `forced_action`
- [ ] Repeating a `request_id` with a different action body returns `409 request_id_conflict`
- [ ] `/start` with an open seat returns `409 seats_not_filled`; a second `/start` is a no-op
- [ ] `Pot.raked_amount` is 0 and `runout_count` is 1 for every hand
- [ ] A short stack whose only raise is all-in (`min_to == max_to`) **still gets `raise` offered**
- [ ] `max_raise_to == stacks[i] + bets[i]`, not `stacks[i]`
- [ ] `pot_awarded.awards[].amount` sums exactly to the pot `amount`, including odd-chip splits
- [ ] Two runs with the same seed produce identical logs once `ts` is stripped
- [ ] `GET /result` returns 200 after the room closes, and 409 before `hand_complete`
- [ ] `POST /actions` response `last_seq` equals the highest `seq` that request emitted
- [ ] Retrying a `request_id` with different `table_talk` but identical action does **not** conflict
- [ ] An illegal action does not reserve its `request_id`
- [ ] Deriving `pots[]` and `pot_total` uses one materialized `tuple(state.pots)`
- [ ] After a 3-way preflop all-in, the board is fully dealt and every live hand exposed
- [ ] `POST /rooms` with `sb >= bb`, `seats` out of range, or `starting_stack < bb` → 400
- [ ] `starting_stacks` of the wrong length, with an entry `< bb`, or supplied alongside
      `starting_stack` → `400 invalid_config`
- [ ] Unequal stacks reach a 3-way all-in with three tiers: `pots[]` has more than one entry,
      `eligible_seats` is correct per tier, and each pot's `awards[]` sums to its `amount`
- [ ] No M1 payload contains `sitting_out`, `busted`, `room_complete`, or `seat_left`
- [ ] Event order for each transition matches §5.0 exactly