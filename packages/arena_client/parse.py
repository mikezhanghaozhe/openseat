"""JSON dict → `packages.engine.types` dataclasses.

The inverse of `packages/room_server/serialize.py`'s `to_wire`: a field
absent from the JSON means `None`, not a sentinel to special-case per call
site. Every `_opt_*` helper here applies that one rule uniformly, mirroring
`to_wire`'s "a `None`-valued field is omitted, not serialized as `null`"
rule from the other direction.

Written as explicit per-type functions rather than a generic/reflective
decoder: the wire shape is small and fixed (§4, §5), and explicit functions
keep every field access checkable by `mypy --strict` without `Any` leaking
past this module's boundary.
"""

from __future__ import annotations

from packages.arena_client.models import (
    HandResult,
    RoomCreated,
    RoomSeatSlot,
    RoomSummary,
)
from packages.engine.types import (
    Action,
    ActionRequiredPayload,
    ActionSpec,
    ActionTakenPayload,
    ActionType,
    Award,
    BlindsPostedPayload,
    BoardDealtPayload,
    ChatMessage,
    Event,
    EventType,
    HandCompletePayload,
    HandStartedPayload,
    HoleCardsDealtPayload,
    Observation,
    Payload,
    Phase,
    Posting,
    PostingKind,
    PotAward,
    PotAwardedPayload,
    PotAwardReason,
    PotView,
    Reveal,
    RoomCompletePayload,
    RoomCreatedPayload,
    SeatJoinedPayload,
    SeatKind,
    SeatLeftPayload,
    SeatStatus,
    SeatTimedOutPayload,
    SeatView,
    ShowdownPayload,
    Street,
    TableTalkPayload,
    YouView,
)

# -- primitive field accessors ------------------------------------------------


def as_dict(value: object) -> dict[str, object]:
    assert isinstance(value, dict), f"expected an object, got {type(value).__name__}"
    return value


def as_list(value: object) -> list[object]:
    assert isinstance(value, list), f"expected an array, got {type(value).__name__}"
    return value


def req_str(d: dict[str, object], key: str) -> str:
    value = d[key]
    assert isinstance(value, str), f"{key!r}: expected a string, got {type(value).__name__}"
    return value


def req_int(d: dict[str, object], key: str) -> int:
    value = d[key]
    assert isinstance(value, int), f"{key!r}: expected an integer, got {type(value).__name__}"
    return value


def req_bool(d: dict[str, object], key: str) -> bool:
    value = d[key]
    assert isinstance(value, bool), f"{key!r}: expected a boolean, got {type(value).__name__}"
    return value


def opt_int(d: dict[str, object], key: str) -> int | None:
    value = d.get(key)
    if value is None:
        return None
    assert isinstance(value, int), f"{key!r}: expected an integer, got {type(value).__name__}"
    return value


def opt_str(d: dict[str, object], key: str) -> str | None:
    value = d.get(key)
    if value is None:
        return None
    assert isinstance(value, str), f"{key!r}: expected a string, got {type(value).__name__}"
    return value


def req_list_str(d: dict[str, object], key: str) -> list[str]:
    return [_expect_str(item) for item in as_list(d.get(key, []))]


def req_list_int(d: dict[str, object], key: str) -> list[int]:
    return [_expect_int(item) for item in as_list(d.get(key, []))]


def req_dict(d: dict[str, object], key: str) -> dict[str, object]:
    return as_dict(d[key])


def _expect_str(value: object) -> str:
    assert isinstance(value, str)
    return value


def _expect_int(value: object) -> int:
    assert isinstance(value, int)
    return value


# -- §3 actions ----------------------------------------------------------------


def parse_action(d: dict[str, object]) -> Action:
    return Action(type=ActionType(req_str(d, "type")), to=opt_int(d, "to"))


def parse_action_spec(d: dict[str, object]) -> ActionSpec:
    return ActionSpec(
        type=ActionType(req_str(d, "type")),
        amount=opt_int(d, "amount"),
        min_to=opt_int(d, "min_to"),
        max_to=opt_int(d, "max_to"),
    )


# -- §4 observation --------------------------------------------------------------


def parse_pot_view(d: dict[str, object]) -> PotView:
    return PotView(index=req_int(d, "index"), amount=req_int(d, "amount"), eligible_seats=req_list_int(d, "eligible_seats"))


def parse_reveal(d: dict[str, object]) -> Reveal:
    return Reveal(
        seat=req_int(d, "seat"),
        hole=req_list_str(d, "hole"),
        rank_class=req_str(d, "rank_class"),
        description=req_str(d, "description"),
    )


def parse_you_view(d: dict[str, object]) -> YouView:
    return YouView(
        seat=req_int(d, "seat"),
        name=req_str(d, "name"),
        hole=req_list_str(d, "hole"),
        stack=req_int(d, "stack"),
        committed_street=req_int(d, "committed_street"),
        committed_hand=opt_int(d, "committed_hand"),
        status=SeatStatus(req_str(d, "status")),
    )


def parse_seat_view(d: dict[str, object]) -> SeatView:
    last_action_raw = d.get("last_action")
    revealed_raw = d.get("revealed")
    return SeatView(
        seat=req_int(d, "seat"),
        name=req_str(d, "name"),
        kind=SeatKind(req_str(d, "kind")),
        stack=req_int(d, "stack"),
        committed_street=req_int(d, "committed_street"),
        status=SeatStatus(req_str(d, "status")),
        last_action=parse_action(as_dict(last_action_raw)) if last_action_raw is not None else None,
        revealed=parse_reveal(as_dict(revealed_raw)) if revealed_raw is not None else None,
    )


def parse_chat_message(d: dict[str, object]) -> ChatMessage:
    return ChatMessage(seq=req_int(d, "seq"), seat=req_int(d, "seat"), name=req_str(d, "name"), text=req_str(d, "text"))


def parse_observation(d: dict[str, object]) -> Observation:
    return Observation(
        protocol_version=req_str(d, "protocol_version"),
        seq=req_int(d, "seq"),
        room_id=req_str(d, "room_id"),
        hand_no=req_int(d, "hand_no"),
        phase=Phase(req_str(d, "phase")),
        to_act=opt_int(d, "to_act"),
        button=req_int(d, "button"),
        you=parse_you_view(req_dict(d, "you")),
        board=req_list_str(d, "board"),
        pots=[parse_pot_view(as_dict(x)) for x in as_list(d.get("pots", []))],
        pot_total=req_int(d, "pot_total"),
        seats=[parse_seat_view(as_dict(x)) for x in as_list(d.get("seats", []))],
        to_call=opt_int(d, "to_call"),
        min_raise_to=opt_int(d, "min_raise_to"),
        max_raise_to=opt_int(d, "max_raise_to"),
        legal_actions=[parse_action_spec(as_dict(x)) for x in as_list(d.get("legal_actions", []))],
        chat=[parse_chat_message(as_dict(x)) for x in as_list(d.get("chat", []))],
        text=req_str(d, "text"),
    )


# -- §5 events ---------------------------------------------------------------------


def parse_posting(d: dict[str, object]) -> Posting:
    return Posting(seat=req_int(d, "seat"), amount=req_int(d, "amount"), kind=PostingKind(req_str(d, "kind")))


def parse_award(d: dict[str, object]) -> Award:
    return Award(seat=req_int(d, "seat"), amount=req_int(d, "amount"))


def parse_pot_award(d: dict[str, object]) -> PotAward:
    return PotAward(
        index=req_int(d, "index"),
        amount=req_int(d, "amount"),
        awards=[parse_award(as_dict(x)) for x in as_list(d.get("awards", []))],
        reason=PotAwardReason(req_str(d, "reason")),
    )


def parse_payload(event_type: EventType, d: dict[str, object]) -> Payload:
    if event_type is EventType.ROOM_CREATED:
        return RoomCreatedPayload(game=req_str(d, "game"), config=req_dict(d, "config"), seats_total=req_int(d, "seats_total"))
    if event_type is EventType.SEAT_JOINED:
        return SeatJoinedPayload(seat=req_int(d, "seat"), name=req_str(d, "name"), kind=SeatKind(req_str(d, "kind")))
    if event_type is EventType.SEAT_LEFT:
        return SeatLeftPayload(seat=req_int(d, "seat"), reason=req_str(d, "reason"))
    if event_type is EventType.HAND_STARTED:
        return HandStartedPayload(hand_no=req_int(d, "hand_no"), button=req_int(d, "button"), stacks=req_list_int(d, "stacks"))
    if event_type is EventType.BLINDS_POSTED:
        return BlindsPostedPayload(postings=[parse_posting(as_dict(x)) for x in as_list(d.get("postings", []))])
    if event_type is EventType.HOLE_CARDS_DEALT:
        return HoleCardsDealtPayload(seats=req_list_int(d, "seats"))
    if event_type is EventType.ACTION_REQUIRED:
        return ActionRequiredPayload(seat=req_int(d, "seat"), deadline_ms=req_int(d, "deadline_ms"))
    if event_type is EventType.ACTION_TAKEN:
        return ActionTakenPayload(
            seat=req_int(d, "seat"),
            action=parse_action(req_dict(d, "action")),
            amount_added=req_int(d, "amount_added"),
            stack_after=req_int(d, "stack_after"),
            pot_after=req_int(d, "pot_after"),
            all_in=req_bool(d, "all_in"),
        )
    if event_type is EventType.BOARD_DEALT:
        return BoardDealtPayload(street=Street(req_str(d, "street")), cards=req_list_str(d, "cards"))
    if event_type is EventType.TABLE_TALK:
        return TableTalkPayload(seat=req_int(d, "seat"), name=req_str(d, "name"), text=req_str(d, "text"))
    if event_type is EventType.SHOWDOWN:
        return ShowdownPayload(reveals=[parse_reveal(as_dict(x)) for x in as_list(d.get("reveals", []))])
    if event_type is EventType.POT_AWARDED:
        return PotAwardedPayload(pots=[parse_pot_award(as_dict(x)) for x in as_list(d.get("pots", []))])
    if event_type is EventType.HAND_COMPLETE:
        return HandCompletePayload(hand_no=req_int(d, "hand_no"), stacks=req_list_int(d, "stacks"), deck=req_list_str(d, "deck"))
    if event_type is EventType.SEAT_TIMED_OUT:
        forced_action_raw = d.get("forced_action")
        return SeatTimedOutPayload(
            seat=req_int(d, "seat"),
            forced_action=ActionType(forced_action_raw) if isinstance(forced_action_raw, str) else None,
        )
    if event_type is EventType.ROOM_COMPLETE:
        return RoomCompletePayload(final_stacks=req_list_int(d, "final_stacks"), ranking=req_list_int(d, "ranking"))
    raise ValueError(f"unknown event type {event_type!r}")


def parse_event(d: dict[str, object]) -> Event:
    event_type = EventType(req_str(d, "type"))
    return Event(
        seq=req_int(d, "seq"),
        type=event_type,
        ts=req_int(d, "ts"),
        payload=parse_payload(event_type, req_dict(d, "payload")),
    )


# -- §6 endpoint envelopes (client-owned shapes, see models.py) ------------------


def parse_room_seat_slot(d: dict[str, object]) -> RoomSeatSlot:
    return RoomSeatSlot(index=req_int(d, "index"), status=req_str(d, "status"), kind=opt_str(d, "kind"), name=opt_str(d, "name"))


def parse_room_created(d: dict[str, object]) -> RoomCreated:
    return RoomCreated(
        room_id=req_str(d, "room_id"),
        invite_token=req_str(d, "invite_token"),
        host_token=req_str(d, "host_token"),
        seats=[parse_room_seat_slot(as_dict(x)) for x in as_list(d.get("seats", []))],
    )


def parse_room_summary(d: dict[str, object]) -> RoomSummary:
    return RoomSummary(
        room_id=req_str(d, "room_id"),
        game=req_str(d, "game"),
        phase=Phase(req_str(d, "phase")),
        seats=[parse_room_seat_slot(as_dict(x)) for x in as_list(d.get("seats", []))],
        hand_no=req_int(d, "hand_no"),
        status=req_str(d, "status"),
    )


def parse_hand_result(d: dict[str, object]) -> HandResult:
    return HandResult(
        hand_no=req_int(d, "hand_no"),
        pots=[parse_pot_award(as_dict(x)) for x in as_list(d.get("pots", []))],
        final_stacks=req_list_int(d, "final_stacks"),
        showdown=[parse_reveal(as_dict(x)) for x in as_list(d.get("showdown", []))],
    )
