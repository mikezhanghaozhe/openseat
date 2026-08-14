"""A trivial local adapter for exercising the room server without a real
game. `packages/game-holdem` does not exist yet and this package must not
depend on it — see AGENTS.md ownership map. Not poker; a pass/fold ring game
used only to drive the REST contract.

The state type (`_StubState`) is private to this module. Nothing outside
`StubAdapter` ever sees it — the room server holds it only as an opaque
`TypeVar` instance (see `adapter.py`).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from packages.engine.types import (
    Action,
    ActionRequiredPayload,
    ActionSpec,
    ActionTakenPayload,
    ActionType,
    Award,
    Event,
    EventType,
    HandCompletePayload,
    HandStartedPayload,
    HoleCardsDealtPayload,
    IllegalAction,
    Observation,
    Phase,
    PotAward,
    PotAwardedPayload,
    PotAwardReason,
    PotView,
    SeatJoinedPayload,
    SeatKind,
    SeatStatus,
    SeatView,
    YouView,
)

_PLACEHOLDER_SEQ = 0
_PLACEHOLDER_TS = 0


def _placeholder_event(event_type: EventType, payload: object) -> Event:
    return Event(seq=_PLACEHOLDER_SEQ, type=event_type, ts=_PLACEHOLDER_TS, payload=payload)  # type: ignore[arg-type]


@dataclass
class _StubState:
    seats_total: int
    active: set[int]
    to_act: int | None
    button: int
    stacks: list[int]
    rounds_left: int
    hand_no: int = 1
    phase: Phase = Phase.PREFLOP
    last_action: dict[int, Action] = field(default_factory=dict)


class StubAdapter:
    # Instance attributes, not class attributes: the `GameAdapter` protocol
    # declares these as plain (non-`ClassVar`) members, and a `ClassVar`
    # implementation fails structural matching under `mypy --strict`.
    def __init__(self) -> None:
        self.id = "stub"
        self.min_players = 2
        self.max_players = 8
        self.config_schema: dict[str, object] = {
            "type": "object",
            "properties": {
                "rounds": {"type": "integer", "minimum": 1},
                "starting_stack": {"type": "integer", "minimum": 0},
            },
            "required": ["rounds"],
            "additionalProperties": True,
        }

    def validate_config(self, cfg: dict[str, object], seats: int) -> None:
        pass  # no cross-field constraints beyond what config_schema already checks

    def reset(self, cfg: dict[str, object], deck: list[str]) -> _StubState:
        raw_rounds = cfg["rounds"]
        assert isinstance(raw_rounds, int)
        rounds = raw_rounds
        raw_starting_stack = cfg.get("starting_stack", 1000)
        assert isinstance(raw_starting_stack, int)
        starting_stack = raw_starting_stack
        # `_seats` is a room-server-injected reserved key, not part of
        # config_schema and not present during config validation — §9's
        # `reset(cfg, deck)` has no other way to convey seat count. See
        # docs/DECISIONS.md.
        raw_seats = cfg["_seats"]
        assert isinstance(raw_seats, int)
        n = raw_seats
        return _StubState(
            seats_total=n,
            active=set(range(n)),
            to_act=0,
            button=0,
            stacks=[starting_stack] * n,
            rounds_left=rounds,
        )

    def setup_events(self, s: _StubState) -> list[Event]:
        return [
            _placeholder_event(
                EventType.HAND_STARTED,
                HandStartedPayload(hand_no=s.hand_no, button=s.button, stacks=list(s.stacks)),
            ),
            _placeholder_event(
                EventType.HOLE_CARDS_DEALT,
                HoleCardsDealtPayload(seats=sorted(s.active)),
            ),
            _placeholder_event(
                EventType.ACTION_REQUIRED,
                ActionRequiredPayload(seat=s.to_act if s.to_act is not None else 0, deadline_ms=0),
            ),
        ]

    def legal_actions(self, s: _StubState, seat: int) -> list[ActionSpec]:
        if s.phase == Phase.HAND_COMPLETE or s.to_act != seat or seat not in s.active:
            return []
        return [ActionSpec(type=ActionType.CHECK), ActionSpec(type=ActionType.FOLD)]

    def apply(self, s: _StubState, seat: int, a: Action) -> list[Event]:
        legal = self.legal_actions(s, seat)
        if a.type not in {spec.type for spec in legal}:
            raise IllegalAction(f"{a.type.value} is not legal for seat {seat}", legal)

        events: list[Event] = []
        s.last_action[seat] = a
        if a.type == ActionType.FOLD:
            s.active.discard(seat)

        events.append(
            _placeholder_event(
                EventType.ACTION_TAKEN,
                ActionTakenPayload(
                    seat=seat,
                    action=a,
                    amount_added=0,
                    stack_after=s.stacks[seat],
                    pot_after=0,
                    all_in=False,
                ),
            )
        )

        if len(s.active) <= 1:
            self._end_hand(s, events)
            return events

        s.to_act = self._next_active(s, seat)
        if s.to_act == s.button:
            s.rounds_left -= 1
        if s.rounds_left <= 0:
            self._end_hand(s, events)
            return events

        events.append(
            _placeholder_event(
                EventType.ACTION_REQUIRED,
                ActionRequiredPayload(seat=s.to_act, deadline_ms=0),
            )
        )
        return events

    def _next_active(self, s: _StubState, seat: int) -> int:
        for offset in range(1, s.seats_total + 1):
            candidate = (seat + offset) % s.seats_total
            if candidate in s.active:
                return candidate
        return seat

    def _end_hand(self, s: _StubState, events: list[Event]) -> None:
        winner = min(s.active) if s.active else 0
        s.phase = Phase.HAND_COMPLETE
        s.to_act = None
        events.append(
            _placeholder_event(
                EventType.POT_AWARDED,
                PotAwardedPayload(
                    pots=[
                        PotAward(
                            index=0,
                            amount=0,
                            awards=[Award(seat=winner, amount=0)],
                            reason=PotAwardReason.UNCONTESTED,
                        )
                    ]
                ),
            )
        )
        events.append(
            _placeholder_event(
                EventType.HAND_COMPLETE,
                HandCompletePayload(hand_no=s.hand_no, stacks=list(s.stacks), deck=[]),
            )
        )

    def view(self, s: _StubState, seat: int) -> Observation:
        seats = [
            SeatView(
                seat=i,
                name="",
                kind=SeatKind.HUMAN,  # overlaid with the real kind by the room server
                stack=s.stacks[i],
                committed_street=0,
                status=SeatStatus.ACTIVE if i in s.active else SeatStatus.FOLDED,
                last_action=s.last_action.get(i),
            )
            for i in range(s.seats_total)
        ]
        you = YouView(
            seat=seat,
            name="",
            hole=[],
            stack=s.stacks[seat],
            committed_street=0,
            committed_hand=None if s.phase == Phase.HAND_COMPLETE else 0,
            status=SeatStatus.ACTIVE if seat in s.active else SeatStatus.FOLDED,
        )
        return Observation(
            protocol_version="",
            seq=0,
            room_id="",
            hand_no=s.hand_no,
            phase=s.phase,
            to_act=s.to_act,
            button=s.button,
            you=you,
            board=[],
            pots=[PotView(index=0, amount=0, eligible_seats=sorted(s.active))],
            pot_total=0,
            seats=seats,
            to_call=None,
            min_raise_to=None,
            max_raise_to=None,
            legal_actions=self.legal_actions(s, seat),
            chat=[],
            text=f"Stub game, seat {seat}, phase {s.phase.value}.",
        )

    def waiting_view(self, cfg: dict[str, object], seats: list[SeatJoinedPayload], seat: int) -> Observation:
        seat_views = [
            SeatView(
                seat=s.seat,
                name=s.name,
                kind=s.kind,
                stack=0,
                committed_street=0,
                status=SeatStatus.ACTIVE,
                last_action=None,
            )
            for s in seats
        ]
        me = next(s for s in seats if s.seat == seat)
        you = YouView(
            seat=me.seat,
            name=me.name,
            hole=[],
            stack=0,
            committed_street=0,
            committed_hand=0,
            status=SeatStatus.ACTIVE,
        )
        return Observation(
            protocol_version="",
            seq=0,
            room_id="",
            hand_no=0,
            phase=Phase.WAITING,
            to_act=None,
            button=0,
            you=you,
            board=[],
            pots=[],
            pot_total=0,
            seats=seat_views,
            to_call=None,
            min_raise_to=None,
            max_raise_to=None,
            legal_actions=[],
            chat=[],
            text="Waiting for the room to start.",
        )

    def is_terminal(self, s: _StubState) -> bool:
        return s.phase == Phase.HAND_COMPLETE

    def results(self, s: _StubState) -> dict[int, float]:
        return {i: (1.0 if i in s.active else 0.0) for i in range(s.seats_total)}
