"""Protocol type definitions — transcribed from docs/PROTOCOL.md.

No logic lives here. This module is imported by every other package, so it
must match the protocol document exactly. If this module and PROTOCOL.md
disagree, the document is right and this file is a bug.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

# ---------------------------------------------------------------------------
# §4 Phase
# ---------------------------------------------------------------------------


class Phase(str, Enum):
    WAITING = "waiting"
    PREFLOP = "preflop"
    FLOP = "flop"
    TURN = "turn"
    RIVER = "river"
    SHOWDOWN = "showdown"
    HAND_COMPLETE = "hand_complete"


# ---------------------------------------------------------------------------
# §1 Street — the betting-round subset of Phase
# ---------------------------------------------------------------------------


class Street(str, Enum):
    PREFLOP = "preflop"
    FLOP = "flop"
    TURN = "turn"
    RIVER = "river"


# ---------------------------------------------------------------------------
# §4 Seat status
# ---------------------------------------------------------------------------


class SeatStatus(str, Enum):
    ACTIVE = "active"
    FOLDED = "folded"
    ALL_IN = "all_in"
    SITTING_OUT = "sitting_out"  # M2-reserved: never emitted in M1
    BUSTED = "busted"  # M2-reserved: never emitted in M1


# ---------------------------------------------------------------------------
# §6 Seat kind
# ---------------------------------------------------------------------------


class SeatKind(str, Enum):
    HUMAN = "human"
    MODEL = "model"
    AGENT = "agent"


# ---------------------------------------------------------------------------
# §5 Event types
# ---------------------------------------------------------------------------


class EventType(str, Enum):
    ROOM_CREATED = "room_created"
    SEAT_JOINED = "seat_joined"
    SEAT_LEFT = "seat_left"  # M2-reserved
    HAND_STARTED = "hand_started"
    BLINDS_POSTED = "blinds_posted"
    HOLE_CARDS_DEALT = "hole_cards_dealt"
    ACTION_REQUIRED = "action_required"
    ACTION_TAKEN = "action_taken"
    BOARD_DEALT = "board_dealt"
    TABLE_TALK = "table_talk"
    SHOWDOWN = "showdown"
    POT_AWARDED = "pot_awarded"
    HAND_COMPLETE = "hand_complete"
    SEAT_TIMED_OUT = "seat_timed_out"
    ROOM_COMPLETE = "room_complete"  # M2-reserved


# ---------------------------------------------------------------------------
# §7 Error codes
# ---------------------------------------------------------------------------


class ErrorCode(str, Enum):
    BAD_REQUEST = "bad_request"
    INVALID_CONFIG = "invalid_config"
    INVALID_TOKEN = "invalid_token"
    NOT_YOUR_TURN = "not_your_turn"
    ROOM_NOT_FOUND = "room_not_found"
    ILLEGAL_ACTION = "illegal_action"
    REQUEST_ID_CONFLICT = "request_id_conflict"
    SEATS_NOT_FILLED = "seats_not_filled"
    SEAT_TAKEN = "seat_taken"
    ROOM_FULL = "room_full"
    HAND_IN_PROGRESS = "hand_in_progress"
    ROOM_CLOSED = "room_closed"
    RATE_LIMITED = "rate_limited"


# ---------------------------------------------------------------------------
# §3 Actions
# ---------------------------------------------------------------------------


class ActionType(str, Enum):
    FOLD = "fold"
    CHECK = "check"
    CALL = "call"
    RAISE = "raise"
    SHOW = "show"
    MUCK = "muck"


@dataclass(frozen=True)
class Action:
    """A single dataclass for all action types rather than a tagged union of
    six classes — the protocol's wire shape is already `{"type": ..., "to": ...}`
    and every other field is `type` alone, so one class with an optional
    field matches §3 without inventing a class hierarchy.
    """

    type: ActionType
    to: int | None = None  # raise-TO semantics; only meaningful for RAISE


@dataclass(frozen=True)
class ActionSpec:
    """An entry in `legal_actions` (§3).

    Bare fold/check/show/muck carry only `type`. `call` carries `amount`.
    `raise` carries `min_to`/`max_to`. All three shapes share one dataclass
    with optional fields rather than a union, matching how the JSON examples
    in §3 differ only by which keys are present.
    """

    type: ActionType
    amount: int | None = None  # present for type == "call"
    min_to: int | None = None  # present for type == "raise"
    max_to: int | None = None  # present for type == "raise"


# ---------------------------------------------------------------------------
# §4 Observation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PotView:
    index: int
    amount: int
    eligible_seats: list[int]


@dataclass(frozen=True)
class YouView:
    seat: int
    name: str
    hole: list[str]
    stack: int
    committed_street: int
    # Omitted from serialization once phase == "hand_complete" (§10:
    # committed_hand is only valid mid-hand), not sent as null.
    committed_hand: int | None
    status: SeatStatus


@dataclass(frozen=True)
class SeatView:
    """A seat as seen by *any* viewer (§4 `seats[]`).

    CRITICAL (invariant 2): there is no `hole` field here, not even as
    `Optional`. `seats[i].hole` does not exist as a field for `i != you.seat`
    per §4's redaction contract — the field is absent from the object, not
    null and not a `["??","??"]` placeholder. A viewer's own hole cards are
    only ever available through `YouView.hole`. `revealed` is the sole,
    explicit exception, populated only after a `showdown` event.
    """

    seat: int
    name: str
    kind: SeatKind
    stack: int
    committed_street: int
    status: SeatStatus
    last_action: Action | None
    revealed: Reveal | None = None  # present only after showdown


@dataclass(frozen=True)
class ChatMessage:
    seq: int
    seat: int
    name: str
    text: str


@dataclass(frozen=True)
class Observation:
    """Returned by `GET /view` and pushed as the WebSocket `state` frame (§4).

    Field order matches the §4 JSON example top to bottom.
    """

    protocol_version: str
    seq: int
    room_id: str
    hand_no: int
    phase: Phase
    to_act: int | None
    button: int

    you: YouView

    board: list[str]
    pots: list[PotView]
    pot_total: int

    seats: list[SeatView]

    # `to_call` is omitted from serialization (not sent as null) when there
    # is no actor — showdown or terminal phases (§10: state.checking_or_
    # calling_amount is `int | None`, and §4's integer shape assumes an
    # actor exists).
    to_call: int | None
    min_raise_to: int | None
    max_raise_to: int | None
    legal_actions: list[ActionSpec]

    chat: list[ChatMessage]

    text: str


# ---------------------------------------------------------------------------
# §5 Events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Event:
    """The envelope every event shares: `{ seq, type, ts, ...payload }`."""

    seq: int
    type: EventType
    ts: int  # epoch milliseconds, matching deadline_ms
    payload: Payload


@dataclass(frozen=True)
class RoomCreatedPayload:
    game: str
    config: dict[str, object]
    seats_total: int
    # No seed or deck here — deliberate, see §10. Room-level room_seed is
    # never transmitted to any client.


@dataclass(frozen=True)
class SeatJoinedPayload:
    seat: int
    name: str
    kind: SeatKind


@dataclass(frozen=True)
class SeatLeftPayload:
    """M2-reserved. Never emitted in M1."""

    seat: int
    reason: str


@dataclass(frozen=True)
class HandStartedPayload:
    hand_no: int
    button: int
    stacks: list[int]


class PostingKind(str, Enum):
    SB = "sb"
    BB = "bb"
    ANTE = "ante"


@dataclass(frozen=True)
class Posting:
    seat: int
    amount: int
    kind: PostingKind


@dataclass(frozen=True)
class BlindsPostedPayload:
    postings: list[Posting]


@dataclass(frozen=True)
class HoleCardsDealtPayload:
    seats: list[int]  # who received cards, never what


@dataclass(frozen=True)
class ActionRequiredPayload:
    seat: int
    deadline_ms: int


@dataclass(frozen=True)
class ActionTakenPayload:
    seat: int
    action: Action
    amount_added: int
    stack_after: int
    pot_after: int
    all_in: bool


@dataclass(frozen=True)
class BoardDealtPayload:
    street: Street  # preflop never appears here — no board exists yet
    cards: list[str]


@dataclass(frozen=True)
class TableTalkPayload:
    seat: int
    name: str
    text: str


@dataclass(frozen=True)
class Reveal:
    seat: int
    hole: list[str]
    rank_class: str
    description: str


@dataclass(frozen=True)
class ShowdownPayload:
    reveals: list[Reveal]


class PotAwardReason(str, Enum):
    UNCONTESTED = "uncontested"
    SHOWDOWN = "showdown"


@dataclass(frozen=True)
class Award:
    seat: int
    amount: int


@dataclass(frozen=True)
class PotAward:
    index: int
    amount: int
    awards: list[Award]  # authoritative — not a winner list, see §5
    reason: PotAwardReason


@dataclass(frozen=True)
class PotAwardedPayload:
    pots: list[PotAward]


@dataclass(frozen=True)
class HandCompletePayload:
    hand_no: int
    stacks: list[int]
    deck: list[str]  # the hand's full 52-card shuffle, disclosed only here, see §10


@dataclass(frozen=True)
class SeatTimedOutPayload:
    """`forced_action` is omitted for showdown-phase timeouts (§5.1) — not
    sent as null. Broadcasting a forced show/muck would reveal whether the
    seat can win from hidden information before any card is shown.
    """

    seat: int
    forced_action: ActionType | None


@dataclass(frozen=True)
class RoomCompletePayload:
    """M2-reserved. Never emitted in M1."""

    final_stacks: list[int]
    ranking: list[int]


# One member per `EventType` value — keeps `Event.payload` narrowable by
# `match`/`isinstance` instead of erased to `object`.
Payload = (
    RoomCreatedPayload
    | SeatJoinedPayload
    | SeatLeftPayload
    | HandStartedPayload
    | BlindsPostedPayload
    | HoleCardsDealtPayload
    | ActionRequiredPayload
    | ActionTakenPayload
    | BoardDealtPayload
    | TableTalkPayload
    | ShowdownPayload
    | PotAwardedPayload
    | HandCompletePayload
    | SeatTimedOutPayload
    | RoomCompletePayload
)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class IllegalAction(Exception):
    """Raised by `GameAdapter.apply` instead of silently correcting state."""

    def __init__(self, reason: str, legal_actions: list[ActionSpec]) -> None:
        """
        Args:
            reason: short human-readable explanation of why the action was rejected.
            legal_actions: the actions the acting seat could legally take instead,
                so the caller (or an error response) can surface them.
        """
        super().__init__(reason)
        self.reason = reason
        self.legal_actions = legal_actions
