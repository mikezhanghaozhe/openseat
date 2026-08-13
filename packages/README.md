# `packages/`

All the Python code for the actual poker room lives here, split into four packages. Each one has a
narrow job, and they only ever talk to each other through plain data (dataclasses) or duck-typed
methods — never by reaching into each other's internals. `docs/PROTOCOL.md` is the spec all four
agree on; `AGENTS.md` has the full invariant list and ownership rules. This file is just the map.

## The four packages, in one line each

```
engine/          Shared vocabulary. Dataclasses and enums only — no logic, no behavior.
room_server/     The FastAPI HTTP server. Runs rooms, tokens, turn order. Doesn't know poker exists.
game_holdem/     The actual poker rules, on top of the pokerkit library.
arena_client/    An HTTP client for the server above — what tests, scripts, and bots talk through.
```

`room_server/` has its own `README.md` with more detail than this one — worth reading if you're
touching that package specifically.

## How a request actually flows

```
 your code / a script / a test
        │  (Python calls)
        ▼
 arena_client.RoomClient           ← packages/arena_client/
        │  (real HTTP, JSON)
        ▼
 room_server's FastAPI app         ← packages/room_server/
        │  (Python calls, in-process — no network here)
        ▼
 HoldemAdapter                     ← packages/game_holdem/
        │  (uses)
        ▼
 pokerkit  (the actual card-game library)
```

Everything above the `pokerkit` line only ever exchanges the plain dataclasses defined in
`engine/types.py` (`Action`, `Event`, `Observation`, ...) — never raw game state, never a pokerkit
object. That's not a style preference; it's the thing that makes redaction (hiding other players'
cards) enforceable by construction instead of by remembering to check. See the "CRITICAL BOUNDARY"
note at the top of `room_server/adapter.py` and `game_holdem/adapter.py` for exactly how.

## The one rule that makes this work: nobody imports anybody else's internals

- **`engine/`** has no logic and no dependencies on the other three. It's just the shapes everyone
  agrees to use — every other package imports *from* it, and never redefines one of its types.
- **`room_server/`** never imports `game_holdem` directly for its actual logic. It only knows about
  a `GameAdapter` *shape* — `reset()`, `apply()`, `view()`, and a few more methods — defined once
  in `room_server/adapter.py` as a `Protocol`. Any object with those methods qualifies, structurally,
  with no inheritance and no import required. `room_server/main.py` optionally registers the real
  `HoldemAdapter` if `game_holdem` happens to be installed (a plain `try`/`except ImportError`) — that
  one spot is the only place the two packages meet, and even that is a soft, optional link, not a
  hard dependency.
- **`game_holdem/`** is the only package allowed to import `pokerkit` (AGENTS.md invariant 7). It
  has no idea `room_server`, `arena_client`, HTTP, or FastAPI exist — it just implements the
  `GameAdapter` shape and returns plain `engine.types` dataclasses.
- **`arena_client/`** doesn't import `room_server` either — it talks to it the same way any outside
  client would, over real HTTP, and turns the JSON responses back into `engine.types` dataclasses on
  the way in.

The payoff: `game_holdem` can be tested with zero knowledge of HTTP or rooms (see its own unit
tests), `room_server` can be tested with a trivial stub game instead of real poker (see
`room_server/stub.py`), and a second game later slots in the same way `game_holdem` did — nothing
about `room_server` has to change for it.

## Where to look next

- `docs/PROTOCOL.md` — the wire format every package is implementing. Start here.
- `AGENTS.md` — invariants, and which package owns which file.
- `packages/room_server/README.md` — how the HTTP server is put together, and the concurrency rule
  that keeps two requests from racing each other inside the same room.
