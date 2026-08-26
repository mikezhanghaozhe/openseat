"""Server-wide configuration read from the environment (docs/PROTOCOL.md §6,
§10)."""

from __future__ import annotations

import os


def allow_fixed_seed() -> bool:
    """Whether room creation may accept a caller-supplied RNG seed.

    Controlled by the `ARENA_ALLOW_FIXED_SEED` env var (must equal the
    string "1"). Off by default — reproducible-but-caller-chosen seeds are a
    testing/debug affordance (see invariant 3, seeded RNG), not something
    that should be reachable in a normal deployment.
    """
    return os.environ.get("ARENA_ALLOW_FIXED_SEED") == "1"
