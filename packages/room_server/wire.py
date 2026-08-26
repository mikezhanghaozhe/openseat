"""Request-parsing helpers shared by the REST surface (`main.py`) and the
WebSocket surface (`ws.py`), so both transports parse `Authorization`
headers and action bodies identically — one code path, not two."""

from __future__ import annotations

from packages.engine.types import Action, ActionType, ErrorCode
from packages.room_server.errors import ApiError

_BEARER_PREFIX = "Bearer "


def bearer_token(authorization: str | None) -> str | None:
    """Extract the bearer token from an `Authorization` header value.

    Args:
        authorization: raw `Authorization` header, or None if absent.

    Returns:
        The token, or None if the header is missing, malformed, or empty after the prefix.
    """
    if authorization is None or not authorization.startswith(_BEARER_PREFIX):
        return None
    token = authorization[len(_BEARER_PREFIX) :].strip()
    return token or None


def parse_action(action_type: str, to: int | None) -> Action:
    """Convert a wire action's `type`/`to` into an engine `Action`.

    Raises:
        ApiError: `BAD_REQUEST` if `action_type` isn't a known `ActionType`.
    """
    try:
        parsed_type = ActionType(action_type)
    except ValueError as exc:
        raise ApiError(ErrorCode.BAD_REQUEST, f"unknown action type {action_type!r}") from exc
    return Action(type=parsed_type, to=to)
