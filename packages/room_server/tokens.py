"""Opaque bearer tokens (docs/PROTOCOL.md §1). Held server-side, compared on
every request. There are no accounts — holding the token is the
authorization."""

from __future__ import annotations

import secrets


def new_room_id() -> str:
    return f"r_{secrets.token_urlsafe(6)[:6]}"


def new_invite_token() -> str:
    return f"inv_{secrets.token_urlsafe(32)}"


def new_host_token() -> str:
    return f"hst_{secrets.token_urlsafe(32)}"


def new_seat_token() -> str:
    return f"sea_{secrets.token_urlsafe(32)}"


def tokens_equal(a: str | None, b: str | None) -> bool:
    if a is None or b is None:
        return False
    return secrets.compare_digest(a, b)
