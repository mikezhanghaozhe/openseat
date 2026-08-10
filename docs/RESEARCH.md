# Human ↔ AI Poker Arena — Research & 48-Hour MVP Plan

**Scope:** Poker (No-Limit Texas Hold'em) as *game #1* on a modular arena, with human + AI seats
in the same browser room, joinable by shared link.

---

## 1. Landscape: what exists and what to take from it

| Project | License | What it actually is | Take / Don't take |
|---|---|---|---|
| [TextArena](https://github.com/LeonGuertler/TextArena) | MIT | 100+ text games, Gym-style `reset/step`, online play + TrueSkill leaderboard | **Take the API shape** (`State`, `add_observation(from_id, to_id)`, `set_winners/set_draw/set_invalid_move`, wrappers). Don't take the code — its "create a game" path is a Python SDK tutorial, which is exactly the friction we're differentiating from |
| [PokerKit](https://github.com/uoftcprg/pokerkit) (UofT CPRG) | MIT | Production-grade multi-variant poker state machine + fast hand evaluation, 99% coverage | **Use as a dependency.** This removes the single biggest 48h risk: side pots, all-in edge cases, min-raise legality, showdown ordering |
| [boardgame.io](https://github.com/boardgameio/boardgame.io) | MIT | Turn-based engine: authoritative `master/`, `playerView` secret-state filtering, phases, lobby, bot framework | **Take the architecture**: server-authoritative reducer + per-player view filter. Reference implementation for "how to not leak hidden info" |
| [sgoedecke/ai-poker-arena](https://github.com/sgoedecke/ai-poker-arena) | check repo | LLMs vs LLMs Hold'em, adversarial eval framing | Prompt/eval framing only |
| [dqnamo/llm-poker](https://github.com/dqnamo/llm-poker) | MIT | Real-time web UI, six models, live reasoning display, equity calc | **Closest visual reference.** Note it's AI-only spectator — no human seats |
| [strangeloopcanon/llm-poker](https://github.com/strangeloopcanon/llm-poker) | check repo | CLI env, strict-JSON actions with retry on invalid | Take the *pattern*: strict JSON + validate + retry |
| [whmmy/poker_LLM](https://github.com/whmmy/poker_LLM) | check repo | Python engine + Vue replay UI + per-player reflection | Replay-viewer idea |
| [PokerBench](https://github.com/pokerllm/pokerbench) (AAAI'25) | dataset | Solver-labelled NLTH decisions, pre/post-flop | Optional: benchmark a seat against GTO baseline |
| RLCard / PettingZoo / OpenSpiel | MIT / Apache | RL environments for poker variants | Only if we later want RL training seats |

**The gap:** every LLM-poker repo found is *AI-vs-AI, watched by a human*. None puts a human at
the table, in a browser, next to model seats, via a link you text to a friend. That's the wedge.

---

## 2. MIT license: how to acknowledge properly

MIT's only real condition: *the copyright notice and permission notice must be included in all
copies or substantial portions of the Software*. Three different situations, three different
obligations:

**A. Installed as a dependency** (`pip install pokerkit`, `npm i ...`)
You are not copying their source into your repo, so strictly there's nothing to do. **But** if you
ship a Docker image, a bundled frontend, or any artifact containing their code, you *are*
distributing it — include the notice. Cheapest correct move: always maintain a
`THIRD_PARTY_NOTICES.md` and generate it (`pip-licenses`, `license-checker`).

**B. Copied / vendored / adapted source files** — obligation is real
- Keep their `LICENSE` text in the vendored directory (`vendor/pokerkit/LICENSE`).
- Put a header on every adapted file:
  ```python
  # Portions adapted from PokerKit (https://github.com/uoftcprg/pokerkit)
  # Copyright (c) 2023 University of Toronto Computer Poker Research Group
  # Licensed under the MIT License. See THIRD_PARTY_NOTICES.md.
  # Modifications (c) 2026 <you>.
  ```
- Note what you changed. Not legally required by MIT, but it's the norm and it protects you.

**C. "We looked at their architecture"** — no legal obligation at all. Ideas, API shapes and
directory layouts aren't what the license covers. Credit them in README anyway; it costs nothing
and it's what the community expects.

**Files to create in the repo root:**

```
LICENSE                  # your own MIT, your copyright, your year
THIRD_PARTY_NOTICES.md   # table: dep | version | license | link | full license text
NOTICE                   # short human-readable credits (optional but nice)
README.md § Acknowledgements
CITATION.cff             # TextArena and PokerKit both explicitly ask to be cited
```

`README.md § Acknowledgements` draft:

> Built on [PokerKit](https://github.com/uoftcprg/pokerkit) (MIT, © UofT CPRG) for poker rules
> and hand evaluation. The environment/agent API is inspired by
> [TextArena](https://github.com/LeonGuertler/TextArena) (MIT) and the server-authoritative
> hidden-state model by [boardgame.io](https://github.com/boardgameio/boardgame.io) (MIT).
> No code from TextArena or boardgame.io is included. Full notices in THIRD_PARTY_NOTICES.md.

**Two traps:**
1. A repo with **no LICENSE file is "all rights reserved"** — you may not copy from it, however
   public it is. Several small LLM-poker repos are in this state. Check before you borrow.
2. MIT code stays MIT. Your project can be any license, but you can't relicense their files.

---

## 3. Architecture

### 3.1 Non-negotiable principle
> **The LLM plays. Deterministic code referees.**

Server holds all state, validates every action, and filters per-seat views. No model — not even as
"dealer" — decides legality, ordering, or winners. This is the failure mode that kills every
naive "AI game master" build: silent hidden-info leaks and wrong pot awards.

### 3.2 Modularity: one `GameAdapter` interface, poker is the first implementation

```python
class GameAdapter(Protocol):
    id: str                  # "holdem-nl"
    min_players: int; max_players: int
    config_schema: dict      # JSON Schema, rendered as the room-creation form

    def reset(self, cfg: dict, seed: int) -> GameState: ...
    def legal_actions(self, s: GameState, seat: int) -> list[ActionSpec]: ...
    def apply(self, s: GameState, seat: int, a: Action) -> list[Event]: ...
    def view(self, s: GameState, seat: int) -> Observation:  # ← redaction lives HERE, once
        ...
    def is_terminal(self, s: GameState) -> bool: ...
    def results(self, s: GameState) -> dict[int, float]: ...
```

`Observation` carries **both** representations so humans and models read the same truth:
```json
{
  "seat": 2, "phase": "flop", "to_act": 2,
  "structured": { "hole": ["Ah","Kd"], "board": ["7c","7d","2s"],
                  "pot": 450, "stacks": [1200, 950, 1300],
                  "to_call": 100, "min_raise": 200, "max_raise": 1300 },
  "text": "You are Seat 2 (BTN)...\nBoard: 7c 7d 2s | Pot: 450 | To call: 100",
  "legal_actions": [ {"type":"fold"}, {"type":"call"},
                     {"type":"raise","min":200,"max":1300} ]
}
```
Redaction is enforced by one function with a unit test that asserts *no other seat's hole cards
appear anywhere in the serialized payload*. Write that test on hour one.

### 3.3 Repo layout

```
/packages
  engine/          # GameAdapter protocol, event log, seeded RNG, redaction + its tests
  game-holdem/     # PokerKit adapter → GameAdapter  (game #2 later drops in beside it)
  room-server/     # WebSocket rooms, seat assignment, turn clock, invite tokens, transcripts
  agent-runtime/   # provider adapters (Anthropic/OpenAI/OpenRouter), JSON action parsing, retry
  agent-sdk/       # BYO-bot client: connect(token) → observe/act loop
  mcp-seat/        # MCP server: join_table / get_view / act  ← the differentiator
  web/             # React table UI, spectator mode, reasoning drawer, replay
```

### 3.4 Wire protocol (one file, single source of truth: `docs/PROTOCOL.md`)

```
→ join   {room, token, display_name}
← state  {Observation}                 # sent only to the entitled seat
← event  {type, seat, payload, ts}     # public, broadcast to room + spectators
→ act    {seat, action, request_id, table_talk?}
← error  {code, reason, legal_actions} # invalid action → re-prompt, do not advance turn
```

---

## 4. How AI agents actually interact — four patterns

Investigated across the projects above; all four are worth knowing, ship 1 + 3, demo 2.

1. **Server-side model seats (default).** Server renders `Observation.text`, calls the provider,
   requires a structured/tool-call action, validates against `legal_actions`, retries ≤2 on
   invalid, folds/checks on third failure. This is what every LLM-poker repo does.
   *Ship this.* Use tool-calling or JSON-schema structured outputs — not regex on free text.

2. **BYO-agent client SDK (TextArena's model).** The agent runs on *the user's* machine and drives
   a loop: `player_id, obs = env.get_observation(); action = agent(obs); done, info = env.step(action)`
   over a WebSocket. Lets people bring their own scaffolding, memory, solver. Thin layer once the
   protocol exists.

3. **MCP seat — the differentiator.** Expose the room as an MCP server with tools
   `list_tables`, `join_table(invite)`, `get_view()`, `act(action)`. Now someone's Claude Code or
   Codex session literally sits down at the table alongside their friends. Nobody in this space
   has shipped that, and it's ~150 lines on top of the WS API.

4. **Webhook bot.** Server POSTs the observation to a URL the user registered, expects an action
   back within N seconds. Easiest for non-Python users. Nice-to-have.

**Anti-cheat note for 2/3/4:** the observation the server sends *is* the redacted view, so an
adversarial client can't see more than its seat. Enforce a turn clock and rate limit.

---

## 5. Tech stack

| Layer | Choice | Why |
|---|---|---|
| Rules engine | **Python + PokerKit** | Side pots and all-in legality are the #1 correctness sink; PokerKit is MIT, tested, and already correct |
| Room server | **FastAPI + WebSockets**, in-memory rooms + append-only JSONL transcript | Same language as the engine — no cross-process state duplication. Postgres/Redis only if time allows |
| Frontend | **Vite + React + TypeScript + Tailwind** | Fastest path; no SSR needed for a room app |
| Agent calls | Provider SDKs behind one `ModelSeat` interface; OpenRouter for breadth | Model-agnostic from day one |
| Deploy | Fly.io / Render (sticky WS) + Vercel/Netlify for the SPA | Avoid serverless-WS pain in a 48h window |

**Alternative if the team is TS-only:** Next.js + PartyKit/`partyserver` on Cloudflare Durable
Objects — one Durable Object per room, WebSockets and persistence for free. Cost: you reimplement
poker rules in TS (`poker-ts`, `pokersolver`) and inherit the side-pot bugs. Only take this if
nobody is comfortable in Python.

---

## 6. Differentiation (what makes this not "another LLM poker repo")

1. **Mixed seats + invite link.** Human, friend, GPT, Claude at one table. Nobody has this.
2. **MCP seat.** Bring your own agent from your own IDE into a live table.
3. **Table talk.** Each seat may emit a public chat message alongside its action; models get the
   chat log in their observation. Poker + language is where LLMs get genuinely interesting —
   bluffing in *words*, not just bet sizes. This is impossible on AI-only spectator sites.
4. **Seeded, replayable transcripts.** Same deck, swap one seat's model, re-run. Turns a party
   game into a cheap eval harness.
5. **Modular from hour one.** Poker ships behind `GameAdapter`, so game #2 (Werewolf, Spyfall) is
   a package, not a rewrite. Keeps the door open to the creator-layer product.

---

## 7. The 48-hour build

Definition of done: *a link you send to a friend; they open it, sit down, and play a hand of
Hold'em against you and two model seats, and you can replay it afterwards.*

| Hours | Deliverable | Gate |
|---|---|---|
| 0–2 | `docs/PROTOCOL.md`, `GameAdapter` types, `AGENTS.md`/`CLAUDE.md` invariants, repo skeleton, LICENSE + THIRD_PARTY_NOTICES | Types compile; contracts frozen |
| 2–8 | PokerKit adapter + redaction + `legal_actions` + seeded RNG, **fully unit-tested headless** | `pytest` green; leak test passes; a full hand runs with 4 scripted seats |
| 8–14 | Room server: create room, invite token, seat assign, turn clock, broadcast, JSONL transcript | Two browser tabs play a hand |
| 14–22 | React table: cards, pot, stacks, action bar, event feed, spectator mode | Playable, ugly is fine |
| 22–30 | Model seats: provider adapters, structured action output, invalid-action retry, reasoning captured per action | AI seat completes 20 hands with zero illegal actions |
| 30–36 | Share link end-to-end + table talk channel + reasoning drawer in UI | Friend on another network joins and plays |
| 36–42 | MCP seat server + `agent-sdk` quickstart | Claude Code joins a table live |
| 42–46 | Replay viewer, polish, deploy, README + attributions | Public URL works |
| 46–48 | Demo script, record a 90s video, buffer | — |

**Cut list, in order, if behind:** replay viewer → spectator mode → `agent-sdk` → table talk.
**Never cut:** the redaction test, the turn clock, invalid-action handling.

---

## 8. Using Codex + Claude Code to actually finish

The trick isn't "more agents" — it's that both work against **contracts you wrote by hand first.**
Spend hours 0–2 yourself on `PROTOCOL.md` + the `GameAdapter` types. Everything after parallelizes.

**Division of labour**

| Give **Codex** (async, parallel, PR-reviewed) | Give **Claude Code** (interactive, in your terminal) |
|---|---|
| Pure, test-verifiable units: hand-evaluator adapter, action parser/validator, observation text renderer, transcript writer/replayer, provider adapters | Cross-cutting architecture: room server + WS + frontend wiring, anything needing live debugging |
| Test suites written from `PROTOCOL.md` | The integration seams where two packages meet |
| Mechanical breadth: 3 provider adapters at once, `THIRD_PARTY_NOTICES.md` generation | Reviewing and merging Codex's PRs, resolving conflicts |
| Refactors with a clear spec | The demo path — driving it end-to-end and fixing what breaks |

**Rules that make this work**

- **One package per agent, `git worktree` per task.** Merge conflicts are what actually eat the
  48 hours. Never point two agents at `room-server/` simultaneously.
- **`AGENTS.md` + `CLAUDE.md` with the invariants**, verbatim: *server is authoritative; `view()`
  is the only place redaction happens; all randomness comes from the seeded RNG; no model output
  is trusted without validation against `legal_actions`.* Agents drift without this.
- **Tests first, then "make these pass without modifying the tests."** Especially for the engine.
- **Keep `PROTOCOL.md` as the single source of truth** and tell every agent to read it first. When
  it changes, change it there and re-run the affected package.
- **Timebox debugging to 20 minutes per agent.** If a Codex PR isn't green in two rounds, pull it
  into Claude Code and pair on it, or throw it away and rewrite the spec.
- **Batch your reviews.** Kick off 3 Codex tasks, work in Claude Code for 45 minutes, review all
  three. Context-switching per PR is what kills throughput.
- **Hour 40 freeze.** No new features after hour 40 — only bug fixes on the demo path.

---

## 9. Risks

| Risk | Mitigation |
|---|---|
| Hidden-info leak into an observation | Single `view()` chokepoint + property test asserting no foreign hole cards in any serialized payload. Hour one. |
| Models emit illegal actions | Structured outputs + validate against `legal_actions` + ≤2 retries + safe default (check/fold) + log every violation as a metric worth showing |
| Side-pot / all-in bugs | Don't write it — PokerKit |
| WebSocket disconnects mid-hand | Turn clock with auto-check/fold; reconnect by seat token; state is server-side so refresh is safe |
| API latency stalls the table | Cap thinking time; run model calls concurrently where seats are independent; stream "thinking…" to keep the UI alive |
| Copying from an unlicensed repo | Check `LICENSE` before reading any repo for code. No LICENSE = don't copy |

---

## 10. Next actions

1. Verify each repo's LICENSE file individually before borrowing anything.
2. Write `docs/PROTOCOL.md` and the `GameAdapter` types by hand.
3. Spike PokerKit for 30 minutes — get one 4-player hand to showdown headless before building anything around it.
4. Create `LICENSE`, `THIRD_PARTY_NOTICES.md`, `CITATION.cff` on day one, not day two.
