#!/usr/bin/env python3
"""Start a room server with the real `holdem-nl` adapter wired in.

`packages/room_server`'s own `make dev` target (`uvicorn
packages.room_server.main:app`) uses that module's default `app`, which
registers only its local `StubAdapter` — `packages/room_server` was built
before `packages/game-holdem` existed and has no reason to import it
(AGENTS.md ownership boundaries; ownership map). `create_app(adapters=...)`
is `room_server`'s own public extension point for exactly this, so this
script uses it rather than editing anything under `packages/room_server/`.
See docs/DECISIONS.md.
"""

from __future__ import annotations

import sys

import uvicorn

from packages.game_holdem.adapter import HoldemAdapter
from packages.room_server.main import create_app

app = create_app(adapters={"holdem-nl": HoldemAdapter()})

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
