"""Client-owned response envelopes.

None of these shapes exist in `packages/engine/types.py` — they're
request/response envelopes specific to individual §6 endpoints (`POST
/rooms`'s `{room_id, invite_token, host_token, seats}`, `/actions`'s
`{first_seq, last_seq, accepted}`, and so on), not protocol domain types.
Wherever a nested piece *is* an engine type (`Event`, `PotAward`, `Reveal`),
it's reused directly rather than re-described here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from packages.engine.types import Event, Phase, PotAward, Reveal


@dataclass(frozen=True)
class RoomSeatSlot:
    """One entry of a room's `seats[]` summary — `POST /rooms`'s 201 body
    and `GET /rooms/{id}` both use this shape (§6)."""

    index: int
    status: str  # "open" | "claimed"
    kind: str | None = None
    name: str | None = None


@dataclass(frozen=True)
class RoomCreated:
    room_id: str
    invite_token: str
    host_token: str
    seats: list[RoomSeatSlot] = field(default_factory=list)


@dataclass(frozen=True)
class SeatClaimed:
    seat_token: str
    seat_index: int


@dataclass(frozen=True)
class StartResult:
    hand_no: int
    to_act: int | None
    first_seq: int
    last_seq: int


@dataclass(frozen=True)
class ActionResult:
    """`POST /actions`'s 200 body. `replayed` is only ever `True` when this
    is the cached result of an earlier identical `request_id` (§6) — never
    sent as `false`, so it defaults to `False` here rather than being
    required."""

    first_seq: int
    last_seq: int
    accepted: bool
    replayed: bool = False


@dataclass(frozen=True)
class EventsPage:
    events: list[Event]
    latest_seq: int


@dataclass(frozen=True)
class RoomSummary:
    room_id: str
    game: str
    phase: Phase
    seats: list[RoomSeatSlot]
    hand_no: int
    status: str


@dataclass(frozen=True)
class HandResult:
    hand_no: int
    pots: list[PotAward]
    final_stacks: list[int]
    showdown: list[Reveal]
