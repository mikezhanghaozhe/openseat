"""Error types raised by `RoomClient` (docs/PROTOCOL.md §7).

Every error body is `{"error": ..., "reason": ..., ...context}`. Two error
codes get dedicated subclasses because the caller's correct response to
each is structurally different from "log it and stop":

- `illegal_action` carries the *current* `legal_actions` in its context —
  the whole point of returning it is so the caller can react (offer the
  player a corrected choice, or a model seat can retry with a legal move)
  instead of just failing. Blindly retrying the same action is wrong;
  blindly giving up and discarding `legal_actions` wastes the one piece of
  information the error was designed to carry.
- `request_id_conflict` means the caller reused an id for a genuinely
  different action body — a bug in the caller, not a transient failure,
  and retrying with the *same* id will never succeed no matter how many
  times it's tried.

Every other error code still raises `ArenaApiError` directly — meaningful
enough to branch on `.error` without needing a dedicated type for each of
the other nine.
"""

from __future__ import annotations

from packages.arena_client.parse import as_dict, parse_action_spec
from packages.engine.types import ActionSpec, ErrorCode


class ArenaApiError(Exception):
    """Raised for every non-2xx response. `.body` is the full parsed error
    JSON, so a caller can always fall back to it even for a code with no
    dedicated subclass."""

    def __init__(self, status_code: int, error: ErrorCode, reason: str, body: dict[str, object]) -> None:
        super().__init__(f"{error.value}: {reason}")
        self.status_code = status_code
        self.error = error
        self.reason = reason
        self.body = body


class IllegalActionError(ArenaApiError):
    @property
    def legal_actions(self) -> list[ActionSpec]:
        raw = self.body.get("legal_actions", [])
        assert isinstance(raw, list)
        return [parse_action_spec(as_dict(item)) for item in raw]


class RequestIdConflictError(ArenaApiError):
    pass


def error_for(status_code: int, body: dict[str, object]) -> ArenaApiError:
    """Build the right exception type for a non-2xx response body. Never
    raises itself — an error body that doesn't parse still produces a
    usable (if generic) `ArenaApiError` rather than an opaque `KeyError`
    from deep inside a caller's try/except."""
    reason_raw = body.get("reason", "")
    reason = reason_raw if isinstance(reason_raw, str) else ""
    error_raw = body.get("error")
    try:
        error = ErrorCode(error_raw) if isinstance(error_raw, str) else None
    except ValueError:
        error = None
    if error is None:
        return ArenaApiError(status_code, ErrorCode.BAD_REQUEST, reason or f"HTTP {status_code}", body)
    if error is ErrorCode.ILLEGAL_ACTION:
        return IllegalActionError(status_code, error, reason, body)
    if error is ErrorCode.REQUEST_ID_CONFLICT:
        return RequestIdConflictError(status_code, error, reason, body)
    return ArenaApiError(status_code, error, reason, body)
