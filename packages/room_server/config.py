"""Server-wide configuration read from the environment (docs/PROTOCOL.md §6,
§10)."""

from __future__ import annotations

import os


def allow_fixed_seed() -> bool:
    return os.environ.get("ARENA_ALLOW_FIXED_SEED") == "1"
