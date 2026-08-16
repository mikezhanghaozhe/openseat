# `room_server`

The M1 REST server: FastAPI, in-memory rooms, every endpoint in `docs/PROTOCOL.md` §6. See
`AGENTS.md` for ownership rules and invariants — this file just explains how the pieces here fit
together.

## Files, in the order a request touches them

```
main.py        FastAPI routes — parses HTTP in, calls Room/RoomStore, serializes JSON out
   │
schemas.py     Pydantic request bodies (what main.py parses the JSON *into*)
   │
store.py       Room + RoomStore — all the actual logic and state lives here
   │
adapter.py     The GameAdapter contract that Room talks to (never a concrete game)
   │
stub.py        A trivial fake game implementing that contract, for local testing

serialize.py   dataclass → JSON dict, used by main.py on the way out
errors.py      ApiError → HTTP status/body, used by main.py's exception handler
tokens.py      random token strings (invite/host/seat), used by store.py
config.py      one env var (ARENA_ALLOW_FIXED_SEED)
```

Nothing here imports a concrete game. `store.py` and `main.py` only ever see the `GameAdapter`
protocol from `adapter.py` — the actual poker (or stub) logic is a black box behind three methods:
`reset`, `apply`, `view`. That's invariant 2 and invariant 7 enforced by construction: there's
literally no `GameState` type in scope to leak.

## What each file does

**`main.py`** — the FastAPI app. One function per endpoint (`POST /rooms`, `POST /seats`,
`POST /start`, `GET /view`, `POST /actions`, `GET /events`, `GET /result`). Each handler is thin:
pull the room out of the store, call one method on it, wrap the result with `protocol_version`.
All error handling is centralized in one `@app.exception_handler(ApiError)` — individual routes
never build error JSON by hand.

**`schemas.py`** — the shape of each request body (`CreateRoomRequest`, `ActionRequest`, etc.), as
Pydantic models. Pydantic validates types automatically before your code ever sees the data
(missing field, wrong type → automatic 422, not something we write by hand).

**`store.py`** — the heart of the package.
- `RoomStore` — a dict of `room_id → Room`, plus the logic to create a new room (validate config,
  pick a room id, wire up the adapter).
- `Room` — one table. Holds seats, tokens, the event log, the current game state, and does
  literally everything: claiming seats, starting the hand, applying actions, building views,
  serving events, computing results. See the queue section below for why it's built this way.

**`adapter.py`** — defines what a "game" has to look like from the room server's point of view:
`reset(cfg, deck) → state`, `apply(state, seat, action) → events`, `view(state, seat) → Observation`,
plus a couple of helper methods (`legal_actions`, `is_terminal`, `results`, `setup_events`). This
is a `Protocol`, not a base class — any object with these methods qualifies, structurally, no
inheritance needed. The state type is a generic `S` that `room_server` never inspects.

**`stub.py`** — `StubAdapter`, a fake "pass or fold" game satisfying that protocol, used so this
package can be built and tested before `packages/game-holdem` exists. Swap it out later by passing
a different adapter into `create_app(adapters=...)`.

**`serialize.py`** — one function, `to_wire`, that turns any dataclass (recursively) into a plain
JSON-safe dict. The one rule that matters: **a field whose value is `None` gets dropped, not sent
as `null`.** That single rule is what implements every "omit this field" note in the protocol doc
(hole cards, `to_call` when there's no actor, `committed_hand` after the hand ends, etc.) — nobody
had to hand-write per-field omission logic.

**`errors.py`** — `ApiError`, one exception class used for every error response. Carries an
`ErrorCode` (which maps to an HTTP status via a lookup table) and a `reason` string. Anywhere in
`store.py` that needs to fail just does `raise ApiError(ErrorCode.NOT_YOUR_TURN, "...")` and
`main.py`'s exception handler turns that into the right JSON + status code automatically.

**`tokens.py`** — generates the three kinds of bearer tokens (`inv_...`, `hst_...`, `sea_...`) and
compares them safely (`secrets.compare_digest`, so token comparison doesn't leak timing info).

**`config.py`** — reads `ARENA_ALLOW_FIXED_SEED` from the environment. That's the only server-wide
setting M1 has.

## The queue: why every mutation goes through `Room.lock`

This is the part worth understanding, because it's the trickiest invariant in the whole server
(invariant 6): **two requests must never both read the game's `to_act` before either one commits
its action.** If that happened, both could see "it's seat 0's turn," both apply, and now the game
has processed two actions as if only one seat acted — corrupted state, wrong `seq` order, a bug
that's very hard to reproduce because it only shows up under real concurrency.

The naive assumption is that because Python has a GIL, this can't happen. It can — FastAPI handlers
are `async`, and every `await` (even ones inside library code you don't see) is a point where the
event loop can switch to a different request. Two `POST /actions` calls for the same room can
genuinely interleave.

The fix: every `Room` method that reads-then-writes state is `async def`, and the *entire*
read-validate-apply-commit sequence happens inside one `async with self.lock:` block:

```python
async def submit_action(self, ...):
    async with self.lock:
        seat = self._seat_by_token(seat_token)      # who's asking
        ... idempotency check ...
        ... closed / not-started checks ...
        obs = self.adapter.view(self.state, seat.index)
        if obs.to_act != seat.index:
            raise ApiError(NOT_YOUR_TURN, ...)        # turn check
        events = self.adapter.apply(self.state, seat.index, action)  # the mutation
        ... stamp seq/ts, append to log, reserve idempotency key ...
    return response
```

Because `asyncio.Lock` only lets one coroutine hold it at a time, a second request for the same
room simply waits at `async with self.lock:` until the first one finishes its entire
read-through-commit sequence. There's no window where two requests can both see the same `to_act`
before either commits — that's the "single ordered queue per room" the protocol asks for, and
`asyncio.Lock` *is* the queue. Requests naturally form a line at that `async with`, are let through
one at a time, in the order they arrived.

Every other room-mutating method (`claim_seat`, `start`) follows the exact same pattern for the
same reason — claiming the last open seat, or starting the hand, has the same "read then write"
shape and the same race if two requests interleave.

**Read-only methods** (`view`, `events_since`, `result`, `summary`) also take the lock, even though
they don't mutate anything. That's not for correctness against torn writes (Python's GIL already
prevents that for a single attribute read) — it's so a read never straddles a concurrent mutation
in the *store*, e.g. reading `self.seq` and `self.events` a moment apart while an action is
mid-commit. Taking the lock guarantees every read sees one consistent, complete snapshot.

**One lock per room, not one global lock.** `RoomStore` also has its own lock, but only for the
brief moment of inserting a newly created room into its dict — once a room exists, all its
contention is scoped to that room's own `asyncio.Lock`. Two different rooms never block each other.
This is also why the protocol says "if `room-server` is ever scaled horizontally, shard by
`room_id`" — the whole design assumes one process owns a room for its lifetime; there's no
cross-process coordination here at all.

## Where `seq` comes from

Two private helpers on `Room` are the *only* places `seq` is ever assigned:

- `_emit(event_type, payload)` — for events the room server itself originates (`room_created`,
  `seat_joined`).
- `_stamp(unstamped_event)` — for events an adapter returned with placeholder `seq=0, ts=0`
  (everything from `setup_events`/`apply`). Room server overwrites both fields before the event is
  real.

Both increment `self.seq` and append to `self.events`, and both are only ever called from inside a
locked section — so `seq` assignment inherits the same one-at-a-time guarantee as everything else.
This is also why the adapter is never trusted to assign `seq`: it doesn't know about other rooms,
other requests, or ordering across them — only the room server, serializing everything through one
lock, can guarantee monotonic order.
