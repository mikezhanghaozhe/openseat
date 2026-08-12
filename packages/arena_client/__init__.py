"""HTTP client for the M1 REST surface (docs/PROTOCOL.md §6, §7).

Internal-first: used by tests/contract/, by model seats (M3), and by the
MCP bridge (M5). Publishing it is a later packaging step, not a design
constraint here.

M1 is REST only — no WebSocket. `RoomClient` wraps `create_room`,
`claim_seat`, `start`, `view`, `act`, `events`, and `result`. WebSocket
support arrives in M2 over the same wire contract.
"""

from packages.arena_client.client import RoomClient
from packages.arena_client.errors import (
    ArenaApiError,
    IllegalActionError,
    RequestIdConflictError,
)
from packages.arena_client.models import (
    ActionResult,
    EventsPage,
    HandResult,
    RoomCreated,
    RoomSeatSlot,
    RoomSummary,
    SeatClaimed,
    StartResult,
)

__all__ = [
    "ActionResult",
    "ArenaApiError",
    "EventsPage",
    "HandResult",
    "IllegalActionError",
    "RequestIdConflictError",
    "RoomClient",
    "RoomCreated",
    "RoomSeatSlot",
    "RoomSummary",
    "SeatClaimed",
    "StartResult",
]
