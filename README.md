# Openseat

A browser room where humans and AI model seats play at the same table, joinable by a shared link.

Most LLM game projects are AI-vs-AI with a human spectating. This one puts a person at the table
next to the models — and lets someone's own agent take a seat, either through a client library or
through MCP, without writing any code.

Poker is game one. The `GameAdapter` interface means game two is a package, not a rewrite.

> **Status: in development.** Nothing here is stable yet.

---

## Quick start

```bash
make install
make dev            # room server on :8000
make hand           # drives four seats to showdown over HTTP
```

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

## Running locally

Two terminals.

**Terminal 1 — backend**

```bash
pip install -r requirements.txt
uvicorn packages.room_server.main:app --reload --port 8000
```

Room server on `http://localhost:8000`.

**Terminal 2 — frontend**

```bash
cd web
npm install
npm run dev
```

Table UI on `http://localhost:5173`.

Create `web/.env.local` (gitignored) with:

```
VITE_API_BASE=http://localhost:8000
```

**Playing with two people locally:** `seat_token` is stored in `localStorage`, keyed by
`room_id`. Two tabs in the same browser share `localStorage`, so the second tab overwrites the
first tab's seat. Use a normal window for one seat and an incognito/private window for the other.

**Fastest smoke test** — create a room, claim a seat, start, fetch a view:

```bash
ROOM_JSON=$(curl -s http://localhost:8000/v1/rooms -X POST -H "Content-Type: application/json" \
  -d '{"game":"holdem-nl","seats":2,"config":{"sb":25,"bb":50,"starting_stack":5000,"turn_seconds":30}}')
ROOM_ID=$(echo "$ROOM_JSON" | jq -r .room_id)
INVITE=$(echo "$ROOM_JSON" | jq -r .invite_token)
HOST=$(echo "$ROOM_JSON" | jq -r .host_token)

SEAT_JSON=$(curl -s http://localhost:8000/v1/rooms/$ROOM_ID/seats -X POST -H "Content-Type: application/json" \
  -d "{\"invite_token\":\"$INVITE\",\"kind\":\"human\",\"display_name\":\"mike\"}")
SEAT_TOKEN=$(echo "$SEAT_JSON" | jq -r .seat_token)

curl -s http://localhost:8000/v1/rooms/$ROOM_ID/seats -X POST -H "Content-Type: application/json" \
  -d "{\"invite_token\":\"$INVITE\",\"kind\":\"human\",\"display_name\":\"alex\"}" > /dev/null

curl -s http://localhost:8000/v1/rooms/$ROOM_ID/start -X POST -H "Content-Type: application/json" \
  -d "{\"host_token\":\"$HOST\"}"

curl -s http://localhost:8000/v1/rooms/$ROOM_ID/view -H "Authorization: Bearer $SEAT_TOKEN"
```

## Deploying

Backend on Render, frontend on Vercel.

**Render (backend)** — see `render.yaml`:

```
buildCommand: pip install -r requirements.txt
startCommand: uvicorn packages.room_server.main:app --host 0.0.0.0 --port $PORT
```

Both `--host 0.0.0.0` and `--port $PORT` are required — Render routes traffic to `$PORT`, and the
default `uvicorn` host (`127.0.0.1`) is not reachable from outside the container.

**Vercel (frontend)** — set `VITE_API_BASE` to the Render URL in the Vercel project's environment
variables **before the first build**. Vite inlines `import.meta.env.*` values at build time, not
runtime — a build made without `VITE_API_BASE` set produces a frontend permanently calling
`undefined`, and redeploying without a rebuild won't fix it.

**Known limits — not bugs:**

- All state is in memory (`RoomStore` holds it in the process). A restart or redeploy drops every
  room.
- Render's free tier sleeps after ~15 minutes idle and takes ~30s to wake. Hit the URL before
  demoing.
- CORS is `allow_origins=["*"]` in `packages/room_server/main.py`. Demo-only.

## Repository layout

```
packages/engine/         Shared types every package imports — no logic, just dataclasses
packages/room_server/    FastAPI rooms, tokens, turn order, broadcast
packages/game_holdem/    PokerKit adapter — the only place poker rules live
packages/arena_client/   HTTP client (WebSocket arrives in M2)
packages/agent-runtime/  Provider adapters, action validation, retry — not built yet (M3)
packages/mcp-seat/       MCP server exposing a seat as tools — not built yet (M5)
web/                     React table UI — not built yet (M4)
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
