"""Opaque bearer tokens (docs/PROTOCOL.md §1). Held server-side, compared on
every request. There are no accounts — holding the token is the
authorization."""

from __future__ import annotations

import secrets


def new_room_id() -> str:
    """Generate a short, URL-safe public room identifier, prefixed `r_`."""
    return f"r_{secrets.token_urlsafe(6)[:6]}"


def new_invite_token() -> str:
    """Generate the `inv_`-prefixed bearer token shared via the room's join link."""
    return f"inv_{secrets.token_urlsafe(32)}"


def new_host_token() -> str:
    """Generate the `hst_`-prefixed bearer token granting host privileges over a room."""
    return f"hst_{secrets.token_urlsafe(32)}"


def new_seat_token() -> str:
    """Generate the `sea_`-prefixed bearer token a player uses to act from one seat."""
    return f"sea_{secrets.token_urlsafe(32)}"


def tokens_equal(a: str | None, b: str | None) -> bool:
    """Constant-time comparison of two bearer tokens.

    Args:
        a: token supplied by the caller (may be missing/None).
        b: token stored server-side to compare against (may be missing/None).

    Returns:
        False if either side is None; otherwise the result of a
        timing-safe comparison (secrets.compare_digest) so token checks
        don't leak length/prefix information via response latency.
    """
    if a is None or b is None:
        return False
    return secrets.compare_digest(a, b)
