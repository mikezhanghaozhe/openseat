# AGENTS.md

Read this file and `docs/PROTOCOL.md` before writing any code. If you only read one other thing,
read `docs/PROTOCOL.md`.

> This file is also the content of `CLAUDE.md` — keep them identical, or symlink one to the other.

---

## What this is

A browser room where humans and AI model seats play the same game at the same table, joinable by
a shared link. Poker is game **one**, not the product. Anything poker-specific lives in
`packages/game-holdem/` and nowhere else.

## Invariants

Violating any of these is a bug even if tests pass.

1. **The server is authoritative.** Clients send intent; the server decides what happened.
2. **`GameAdapter.view(state, seat)` is the only place redaction happens.** No endpoint, no
   WebSocket frame, and no log line may serialize game state by any other path.
3. **All randomness comes from the seeded RNG.** No `random.random()`, no `time.time()` in game
   logic. Same seed + same actions ⇒ byte-identical event log.
4. **No player input is trusted** — human, model, or agent. Every action is validated against
   `legal_actions` before it touches state.
5. **`seq` is monotonic per room** and appears on every view and event. Reconnect, replay, and
   polling all depend on it.
6. **Secrets never leave their scope.** BYOK API keys live in memory for the room's lifetime.
   Never persisted, never logged, never in a response body.
7. **Only `packages/game-holdem/` may import `pokerkit`.** If you find yourself reaching for it
   anywhere else, the abstraction is wrong — stop and say so.

## Ownership map

One agent per package. Never two agents on the same file.

| Path | Owner | Contents |
|---|---|---|
| `packages/engine/` | agent A | `GameAdapter` protocol, event log, seeded RNG, redaction helpers |
| `packages/game-holdem/` | agent B | PokerKit adapter — the only place poker rules exist |
| `packages/arena-client/` | agent C | HTTP + WebSocket client; used by tests, model seats, MCP bridge |
| `packages/agent-runtime/` | agent D | Provider adapters, action parsing, retry, reasoning capture |
| `packages/room-server/` | **human, interactively** | FastAPI, rooms, tokens, turn clock, broadcast |
| `web/` | agent E | React table UI |
| `docs/PROTOCOL.md`, shared types | **human only** | Never edited by an agent |

## Rules for agent tasks

- **Do not modify anything under `tests/contract/`.** Those are the spec. If a contract test looks
  wrong, stop and say so rather than editing it.
- Add your own tests under `tests/unit/` freely.
- Append one line to `docs/DECISIONS.md` whenever you make a non-obvious choice. That file is how
  other agents find out why you did something.
- Stay inside your package. If your task needs a change in someone else's package or in
  `PROTOCOL.md`, stop and flag it — do not reach across.
- If `PROTOCOL.md` and the code disagree, the document is right and the code is a bug.

## Commands

```
make install       # venv + deps
make test          # contract tests + unit tests
make test-contract # contract tests only — this is the gate
make dev           # run the room server on :8000
make hand          # scripts/play_hand.sh — drives 4 seats to showdown over HTTP
make lint
```

## Style

- Python 3.11+, type hints on every public function, `ruff` + `mypy` clean.
- Integers only in the money path. No floats anywhere near chips.
- Raise `IllegalAction` rather than silently correcting anything.
- No bare `except:`. No swallowed errors in the action path.

## Current milestone

**M1 — playable through the API.** REST only, no WebSocket, no browser, no LLM. Gate:
`make hand` drives four seats to showdown; the leak test passes; the same seed reproduces the same
event log. See `docs/MILESTONES.md`.
