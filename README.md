# Arena

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

## Repository layout

```
packages/engine/         GameAdapter protocol, event log, seeded RNG, redaction
packages/game-holdem/    PokerKit adapter — the only place poker rules live
packages/arena-client/   HTTP + WebSocket client
packages/agent-runtime/  Provider adapters, action validation, retry
packages/room-server/    FastAPI rooms, tokens, turn clock, broadcast
packages/mcp-seat/       MCP server exposing a seat as tools
web/                     React table UI
docs/PROTOCOL.md         Wire protocol — read this first
docs/DECISIONS.md        Append-only log of non-obvious choices
tests/contract/          The spec as executable tests
```

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
