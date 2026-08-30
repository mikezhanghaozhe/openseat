# Openseat

A browser room where humans and AI model seats play at the same table, joinable by a shared link.

Most LLM game projects are AI-vs-AI with a human spectating. This one puts a person at the table
next to the models — and lets someone's own agent take a seat, either through a client library or
through MCP, without writing any code.

Poker is game one. The `GameAdapter` interface means game two is a package, not a rewrite.

> **Status: in development.** Nothing here is stable yet.

---

## Setup (once per machine)

Backend and frontend are separate toolchains (Python vs. Node) with separate installs.

```bash
make install        # backend: creates .venv, installs Python deps
cd web && npm ci     # frontend: installs Node deps from the committed lockfile
cd ..
```

## Quick check: backend only, no browser

```bash
make hand
```
Starts its own throwaway server, drives four seats to a full showdown over HTTP, and tears the
server down. Good for a fast sanity check without touching the web client at all.

## Running the full app (backend + web client)

Needs three terminals.

If any seat will use `kind: "model"`, export a house API key first — model seats fail to claim
without it:
```bash
export OPENROUTER_API_KEY=<your-key>
```

**Terminal 1 — backend.** `make dev` runs
`uvicorn packages.room_server.main:app --reload --reload-dir packages --port 8000` (see
`Makefile`). To get reproducible hands, set the fixed-seed flag first:
```bash
ARENA_ALLOW_FIXED_SEED=1 make dev
```

**Terminal 2 — web client:**
```bash
cd web
VITE_API_URL=http://localhost:8000 npm run dev
```
`VITE_API_URL` is the backend URL the client calls — it's never hardcoded (see `web/src/api.ts`),
so this must point at wherever Terminal 1 is actually listening. Vite prints the local URL to
open, e.g. `http://localhost:5173`.

**Terminal 3 — create a room:**
```bash
curl -s -X POST http://localhost:8000/v1/rooms \
  -H "Content-Type: application/json" \
  -d '{"game":"holdem-nl","seats":2,"config":{"sb":25,"bb":50,"starting_stack":5000,"turn_seconds":30},"seed":42}'
```
The response has `room_id`, `invite_token`, and `host_token`. **Copy all three** — you'll
substitute them into the commands below.

Open two browser tabs (one per seat), replacing `<invite_token>` and `<room_id>` with your values:
```
http://localhost:5173/join/<invite_token>?room=<room_id>
```
Claim seat 0 in one tab, seat 1 in the other.

Once both seats are claimed, start the hand — replace `<room_id>` and `<host_token>`:
```bash
curl -s -X POST http://localhost:8000/v1/rooms/<room_id>/start \
  -H "Content-Type: application/json" \
  -d '{"host_token":"<host_token>"}'
```

Both tabs should flip to a live table — hole cards, pot, and (on whichever seat is `to_act`) an
action bar with fold/call/raise. Play the hand out and confirm:
- check and call never both appear
- the raise slider stays within `min_to`/`max_to`
- the event feed fills in on the right
- pot/showdown display appears at `hand_complete`, with a MAIN/SIDE line per pot if any all-in
  created side pots (use unequal `starting_stacks` in the room-creation body to force that)
- refreshing a tab mid-hand reconnects cleanly and replays state (seat_token persists in
  `localStorage`, keyed by `room_id`)

## How a game works

The server is authoritative. It holds all state, validates every action, and sends each seat only
its own redacted view. No model ever referees — models play, deterministic code decides.

Read `docs/PROTOCOL.md` for the wire format. It is the single source of truth; if the code and the
document disagree, the document is right.

## Taking a seat

| As | How |
|---|---|
| Human | Open the invite link |
| Model seat | Add a seat with `kind: "model"`; runs server-side |
| Your own bot | `pip install arena-client`, connect with a seat token |
| Your assistant | Point an MCP client at the room's MCP endpoint |

## Repository layout

```
packages/engine/         Shared types every package imports — no logic, just dataclasses
packages/room_server/    FastAPI rooms, tokens, turn order, broadcast
packages/game_holdem/    PokerKit adapter — the only place poker rules live
packages/arena_client/   HTTP client (WebSocket arrives in M2)
packages/agent-runtime/  Provider adapters, action validation, retry — not built yet (M3)
packages/mcp-seat/       MCP server exposing a seat as tools — not built yet (M5)
web/                     React table UI (M4)
docs/PROTOCOL.md         Wire protocol — read this first
docs/DECISIONS.md        Append-only log of non-obvious choices
tests/contract/          The spec as executable tests
```

See `packages/README.md` for how the four existing packages fit together and why none of them
import each other's internals.

## Contributing

`AGENTS.md` describes the invariants and the ownership map. Contract tests under `tests/contract/`
are the spec — if one looks wrong, open an issue rather than editing it.

## License

MIT. See `LICENSE`.

## Acknowledgements

Built on [PokerKit](https://github.com/uoftcprg/pokerkit) (MIT, © University of Toronto Computer
Poker Research Group) for poker rules and hand evaluation.

The environment/agent API shape is inspired by
[TextArena](https://github.com/LeonGuertler/TextArena) (MIT), and the server-authoritative model
with per-player view filtering by [boardgame.io](https://github.com/boardgameio/boardgame.io)
(MIT). No code from either project is included.

Full notices in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
