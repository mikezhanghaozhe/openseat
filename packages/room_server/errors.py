"""API error type and its HTTP mapping (docs/PROTOCOL.md §7).

Every error body is built only from the request and the caller's own
redacted `Observation` — never from raw game state. Callers pass only
JSON-safe `context` (e.g. `legal_actions` already converted via
`serialize.to_wire`), never a `GameState` or adapter-internal object.
"""

from __future__ import annotations

from packages.engine.types import ErrorCode

_STATUS_BY_CODE: dict[ErrorCode, int] = {
    ErrorCode.BAD_REQUEST: 400,
    ErrorCode.INVALID_CONFIG: 400,
    ErrorCode.INVALID_TOKEN: 401,
    ErrorCode.NOT_YOUR_TURN: 403,
    ErrorCode.ROOM_NOT_FOUND: 404,
    ErrorCode.ILLEGAL_ACTION: 409,
    ErrorCode.REQUEST_ID_CONFLICT: 409,
    ErrorCode.SEATS_NOT_FILLED: 409,
    ErrorCode.SEAT_TAKEN: 409,
    ErrorCode.ROOM_FULL: 409,
    ErrorCode.HAND_IN_PROGRESS: 409,
    ErrorCode.ROOM_CLOSED: 410,
    ErrorCode.RATE_LIMITED: 429,
}


class ApiError(Exception):
    def __init__(self, code: ErrorCode, reason: str, **context: object) -> None:
        super().__init__(reason)
        self.code = code
        self.status_code = _STATUS_BY_CODE[code]
        self.reason = reason
        self.context = context

    def body(self) -> dict[str, object]:
        return {"error": self.code.value, "reason": self.reason, **self.context}
