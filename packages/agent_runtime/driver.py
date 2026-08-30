"""API-key resolution for a claimed model seat (docs/MILESTONES.md M3:
"House key from OPENROUTER_API_KEY. BYOK held in memory for the room's
lifetime only.").

This is the only place a house key is read from the environment, and the
only place a BYOK key is checked for presence — both at claim time, so a
misconfigured seat fails `POST /seats` instead of failing silently on its
first turn. The resolved key is handed back to the caller (`packages/
room_server/store.py`) to hold in memory for the room's lifetime; nothing in
this package stores it anywhere longer-lived than that.
"""

from __future__ import annotations

import os

from packages.agent_runtime.types import ModelSeatSpec

HOUSE_KEY_ENV = "OPENROUTER_API_KEY"


class HouseKeyMissingError(Exception):
    """Raised when a `key_mode: "house"` seat is claimed but no house key is configured."""


def resolve_api_key(spec: ModelSeatSpec) -> str:
    """Resolve the API key `spec` should use for provider calls.

    Raises:
        ValueError: `key_mode == "byok"` but `spec.api_key` is empty.
        HouseKeyMissingError: `key_mode == "house"` but `OPENROUTER_API_KEY` isn't set.
    """
    if spec.key_mode == "byok":
        if not spec.api_key:
            raise ValueError("api_key is required when key_mode is 'byok'")
        return spec.api_key
    if spec.key_mode != "house":
        raise ValueError(f"unknown key_mode {spec.key_mode!r}")
    house_key = os.environ.get(HOUSE_KEY_ENV)
    if not house_key:
        raise HouseKeyMissingError(f"{HOUSE_KEY_ENV} is not set; cannot seat a house-key model seat")
    return house_key
