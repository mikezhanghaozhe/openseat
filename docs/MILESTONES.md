# Poker Arena — Milestones, API Surface & AI Workflow

Companion to `POKER_ARENA_MVP.md`. Five milestones, each with a hard gate.
**Rule: you do not start milestone N+1 until N's gate passes.**

---

## M1 — Playable through the API (hours 0–10)

The whole game works over plain HTTP, driven by curl. No WebSocket, no browser, no LLM.
This is deliberate: `GET /view` + `POST /actions` is the same contract the WebSocket will carry,
just polled. Building it REST-first means the engine never depends on the transport, and every
later client (WS, agent-sdk, MCP, webhook) is a wrapper over a surface that already works.

**Hours 0–2 are yours, not an agent's.** Write `docs/PROTOCOL.md`, the `GameAdapter` types, and the
JSON schemas by hand. Everything downstream parallelizes off these; if they're vague, three agents
build three incompatible things.

### Endpoints

```
POST   /v1/rooms
       { game: "holdem-nl", seats: 4, config: { sb: 25, bb: 50, stack: 5000 }, seed?: 42 }
    →  201 { room_id, invite_token, host_token, seats: [{index, status:"open"}] }

POST   /v1/rooms/{id}/seats
       { invite_token, seat: 2, kind: "human"|"model"|"agent", display_name }
    →  201 { seat_token, seat_index }            # seat_token is the credential for all actions

GET    /v1/rooms/{id}
    →  200 { room_id, game, phase, seats[], hand_no, status }   # public, no hidden info ever

POST   /v1/rooms/{id}/start        { host_token }
    →  200 { hand_no: 1, to_act: 0 }

GET    /v1/rooms/{id}/view?seat_token=...
    →  200 {
         seq, seat, phase, to_act,
         structured: { hole, board, pot, stacks, to_call, min_raise, max_raise, positions },
         text: "You are Seat 2 (BTN). Board: 7c 7d 2s | Pot: 450 | To call: 100",
         legal_actions: [ {type:"fold"}, {type:"call"}, {type:"raise", min:200, max:1300} ]
       }

POST   /v1/rooms/{id}/actions
       { seat_token, request_id, action: {type:"raise", amount:200}, table_talk?: "..." }
    →  200 { seq, accepted: true }
    →  409 { error:"illegal_action", reason, legal_actions:[...] }   # turn does NOT advance
    →  403 { error:"not_your_turn" }

GET    /v1/rooms/{id}/events?since=0
    →  200 { events: [ {seq, type, seat, payload, ts} ] }   # public log; also the replay source

GET    /v1/rooms/{id}/result
    →  200 { hand_no, winners, pots, stacks, showdown }
```

Design notes worth locking in now:
- `seq` is monotonic across the room and appears on every view and event. It is what makes
  reconnect, replay, and polling all work. Add it in M1 or retrofit it painfully in M2.
- `request_id` makes `POST /actions` idempotent — a retried request must not double-raise.
- Redaction happens in exactly one function (`GameAdapter.view`). Every endpoint calls it.
- Seed in, seed out: same seed + same action sequence ⇒ byte-identical event log.

### Gate
- [ ] `scripts/play_hand.sh` drives 4 seats to showdown through HTTP only
- [ ] Leak test: no seat's `view` response contains any other seat's hole cards, at any phase
- [ ] Determinism test: same seed + same actions ⇒ identical event log
- [ ] Illegal action returns 409 and the turn pointer is unchanged
- [ ] Side pot test: 3-way all-in with unequal stacks pays out correctly (PokerKit does this — assert it)

---

## M2 — Realtime rooms (hours 10–18)

Add push. The REST endpoints stay — they become the fallback and the debugging surface.

```
WS     /v1/rooms/{id}/ws?seat_token=...
  ← hello   { seq, seat, replay: [events...] }
  ← state   { ...same shape as GET /view }        # private, only to the entitled seat
  ← event   { seq, type, seat, payload, ts }      # public broadcast
  → act     { request_id, action, table_talk? }
  ← error   { code, reason, legal_actions }
  ← clock   { seat, deadline_ms }
```

Plus: server-side turn clock with auto check/fold, reconnect-by-seq, spectator connections
(receive `event` only, never `state`), append-only JSONL transcript per room.

### Gate
- [ ] Two `wscat` clients play a hand with push only
- [ ] Kill one client mid-hand, reconnect with same seat_token, state is correct
- [ ] Turn clock fires and auto-folds an unresponsive seat
- [ ] Spectator connection provably cannot receive a `state` frame

---

## M3 — Model seats (hours 18–26)

```
POST   /v1/rooms/{id}/seats
       { invite_token, seat: 3, kind: "model",
         model: "anthropic/claude-sonnet-4.5",
         key_mode: "house" | "byok", api_key?: "sk-..." }   # byok held in memory only, never logged

GET    /v1/rooms/{id}/seats/{i}/reasoning?seat_token=...   # host/spectator view of why it acted
```

One `ModelSeat` interface, `decide(observation) -> Action`. OpenRouter adapter first (one key,
one base URL, 200+ models = a dropdown instead of an integration per provider). Structured
outputs / tool calling — never regex on prose. Validate → retry ≤2 → default check/fold → log the
violation as a metric.

### Gate
- [ ] 20 hands, 4 model seats, zero illegal actions committed to state
- [ ] Cost and latency logged per decision
- [ ] Illegal-action rate emitted per model (this is a feature, not just telemetry)
- [ ] BYOK key never appears in logs, transcripts, or the events endpoint

---

## M4 — Browser table + share link (hours 26–38)

React table UI, invite link flow, spectator mode, per-seat reasoning drawer, table talk channel.
Ugly is acceptable; broken is not.

```
GET    /join/{invite_token}     →  SPA route: pick a seat, get seat_token, connect WS
```

### Gate
- [ ] A friend on a different network opens the link and completes a hand against you + 2 model seats
- [ ] Refresh mid-hand loses nothing
- [ ] Table talk from a model seat appears in the chat log and in other seats' observations

---

## M5 — Agent front doors (hours 38–46)

- `agent-sdk` (Python + TS): `table = connect(url, seat_token)` → `obs = table.observe()` → `table.act(...)`
- MCP server: `list_tables`, `join_table(invite)`, `get_view()`, `wait_for_turn()`, `act(action)`
  - `wait_for_turn()` long-polls. Without it the model burns tokens polling `get_view()`.
  - The bridge process holds the WebSocket and buffers the latest view so tools return instantly.
- Replay viewer over `GET /events`

### Gate
- [ ] A Claude Code session joins a live table via MCP and plays a hand
- [ ] A 30-line bot using agent-sdk plays a hand
- [ ] A saved transcript replays deterministically

**Hours 46–48: freeze. Demo script, README, attribution files, recorded video. No new code.**

---

## Recommended AI workflow

### Which surface

**Not artifacts.** Artifacts can't run a server, hold a repo, or run pytest. Use them only for
throwing a table-layout mockup on screen in 60 seconds before M4.

| Surface | Use it for | Don't use it for |
|---|---|---|
| **Claude Code in VS Code** — your primary seat | M1 contracts, M2 server, M4 wiring, all integration, anything where you need to watch a log | Well-specified isolated units (waste of your attention) |
| **Claude Code on the web → PR** | Tasks fully specified by a doc you already wrote, verifiable by tests, touching one package | Anything requiring a running server or live debugging |
| **Codex, parallel worktrees** | Mechanical breadth: 3 provider adapters, the text renderer, transcript writer, MCP tool wrappers | The protocol, the redaction layer, merge-heavy work |

The VS Code extension over the bare terminal mostly buys you inline diff review and file context.
Either is fine. What matters is that *one* interactive session is the integrator and everything
else produces PRs into it.

### Allocating tests across parallel agents

**The agent that writes the implementation must not write its acceptance test.** This is the whole
trick — otherwise the agent writes a test that passes for its implementation and you learn nothing.

1. You (or one dedicated session) write **contract tests** from `docs/PROTOCOL.md` first. Commit
   them to `main`, failing, under `tests/contract/`.
2. Each parallel agent gets: one package, its failing contract tests, and the instruction
   *"make these pass; do not modify anything under `tests/contract/`."*
3. Agents may add their own `tests/unit/` freely — that's their scratchpad.
4. CI runs `tests/contract/` on every PR. This is what lets you review three PRs in five minutes
   instead of three at a time. Worth the 20 minutes to set up.

**Ownership map — one agent per package, never two on the same file:**

```
engine/         → agent A     (types, redaction, event log, seeded RNG)
game-holdem/    → agent B     (PokerKit adapter)
agent-runtime/  → agent C     (provider adapters, action parsing, retry)
web/            → agent D     (React table)
room-server/    → YOU, interactively
docs/PROTOCOL.md + shared types → YOU only, always
```

`git worktree add ../wt-holdem feat/holdem` per task. Merge conflicts are what actually consume a
48-hour window, not writing code.

### Sharing context between agents

Agents share nothing at runtime. They share through **files in the repo**, so make those files
carry the context:

- **`docs/PROTOCOL.md`** — wire protocol, endpoint shapes, error codes. Single source of truth.
- **`AGENTS.md` / `CLAUDE.md` at root** — invariants stated verbatim, because agents drift without
  them: *server is authoritative; `GameAdapter.view()` is the only place redaction happens; all
  randomness comes from the seeded RNG; no model output is trusted without validation against
  `legal_actions`.* Plus commands (`make test`, `make dev`) and the ownership map above.
- **Per-package `AGENTS.md`** — that package's contract and its boundaries.
- **`docs/DECISIONS.md`** — append-only. Every agent appends one line when it makes a non-obvious
  choice. This is how agent C finds out why agent A structured events the way it did.
- **A single `types` package everything imports.** A contract change then breaks the build loudly
  instead of letting two packages silently diverge.

Every task prompt starts with *"read `AGENTS.md` and `docs/PROTOCOL.md` first."* That sentence is
the context handoff.

### Cadence

Kick off 2–3 async tasks → work 45 minutes in VS Code → review all PRs in one batch → repeat.
Reviewing one PR at a time is what kills throughput.

Timebox debugging to 20 minutes per agent. If a PR isn't green after two rounds, either pull it
into your interactive session and pair on it, or throw it away and rewrite the spec — the spec was
the problem.
