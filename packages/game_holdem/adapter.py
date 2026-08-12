"""No-Limit Texas Hold'em `GameAdapter` (docs/PROTOCOL.md §9, §10).

`GameState` here is a private wrapper around `pokerkit.State`. Nothing
outside this module ever sees a raw `pokerkit.State` — `view()` is the only
place a `GameState` is converted into the protocol's `Observation`, which is
exactly what makes it the sole redaction chokepoint (invariant 2). Never
pass `GameState`/`pokerkit.State` into a rendering or serialization
function; once a parameter is typed `Observation`, mypy prevents a leak,
but nothing stops one through a raw state — so raw state simply never
crosses this module's boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pokerkit import Automation, Mode, NoLimitTexasHoldem, StandardHighHand, State

from packages.engine.types import (
    Action,
    ActionRequiredPayload,
    ActionSpec,
    ActionTakenPayload,
    ActionType,
    Award,
    BlindsPostedPayload,
    BoardDealtPayload,
    Event,
    EventType,
    HandCompletePayload,
    HandStartedPayload,
    HoleCardsDealtPayload,
    IllegalAction,
    Observation,
    Phase,
    Posting,
    PostingKind,
    PotAward,
    PotAwardedPayload,
    PotAwardReason,
    PotView,
    Reveal,
    SeatKind,
    SeatStatus,
    SeatView,
    ShowdownPayload,
    Street,
    YouView,
)
from packages.game_holdem import cards

# HOLE_DEALING, BOARD_DEALING, RUNOUT_COUNT_SELECTION, HOLE_CARDS_SHOWING_OR_MUCKING,
# and CHIPS_PUSHING are deliberately excluded — we drive all five ourselves. See
# docs/DECISIONS.md "game-holdem: automation choices" for why each one is manual.
_AUTOMATIONS = (
    Automation.ANTE_POSTING,
    Automation.BET_COLLECTION,
    Automation.BLIND_OR_STRADDLE_POSTING,
    Automation.CARD_BURNING,
    Automation.HAND_KILLING,
    Automation.CHIPS_PULLING,
)

_STREET_ORDER = (Street.FLOP, Street.TURN, Street.RIVER)
_STREET_TO_PHASE = {Street.FLOP: Phase.FLOP, Street.TURN: Phase.TURN, Street.RIVER: Phase.RIVER}


@dataclass
class GameState:
    """Private to this package. The room server (and every other package)
    holds this only as an opaque `TypeVar` instance — see the CRITICAL
    BOUNDARY note in `packages/room_server/adapter.py`."""

    pk: State
    hand_no: int
    button: int
    seats_total: int
    starting_stack: int
    sb: int
    bb: int
    ante: int
    remaining_deck: list[str]
    full_deck: list[str]
    seat_status: list[SeatStatus]
    last_action: dict[int, Action] = field(default_factory=dict)
    revealed: dict[int, Reveal] = field(default_factory=dict)
    board_deals_done: int = 0
    phase: Phase = Phase.PREFLOP
    awaiting_showdown_seat: int | None = None
    # seat -> hole cards, captured immediately at show time (see _build_reveal).
    pending_all_in_reveals: dict[int, list[str]] = field(default_factory=dict)
    hand_had_showdown: bool = False


def _int(value: object) -> int:
    assert isinstance(value, int)
    return value


def _unstamped(event_type: EventType, payload: object) -> Event:
    return Event(seq=0, type=event_type, ts=0, payload=payload)  # type: ignore[arg-type]


def _pot_total(pk: State) -> int:
    return sum(p.amount for p in tuple(pk.pots)) + sum(pk.bets)


def _board(pk: State) -> list[str]:
    return [repr(c) for group in pk.board_cards for c in group]


def _build_reveal(seat: int, hole: list[str], board: list[str]) -> Reveal:
    """Evaluate a hand independently of `pokerkit.State`'s own bookkeeping —
    not `state.get_up_hands()`. `Automation.HAND_KILLING` calls
    `_muck_hole_cards()` on every seat that can't win once a winner is
    determined, which *clears* `state.hole_cards[seat]` as an ordinary part
    of end-of-hand cleanup (verified against pokerkit source, not assumed —
    it is not documented behavior). By the time a losing seat's hand would
    be evaluated for the wire event, pokerkit may have already erased the
    very data needed to evaluate it. `StandardHighHand.from_game` takes
    hole/board cards directly and has no dependency on `State` at all, so
    it works regardless of what hand-killing has already done — as long as
    the hole cards were captured *before* that could happen. See
    docs/DECISIONS.md."""
    hand = StandardHighHand.from_game("".join(hole), "".join(board))
    return Reveal(seat=seat, hole=hole, rank_class=hand.entry.label.name, description=str(hand))


def _advance(s: GameState) -> list[Event]:
    """Drive every automatic step (board dealing, forced all-in reveals,
    pot awarding) until either a real decision is needed from a seat
    (betting or a discretionary show/muck) or the hand is fully resolved.

    Priority order matches what pokerkit actually requires operationally
    (verified empirically against 0.7.4, not assumed from docs — see
    docs/DECISIONS.md): showdown reveals for an all-in hand happen *before*
    the remaining board is dealt, not after. `can_deal_board()` stays
    `False` until every seat in `showdown_indices` for that round has shown
    or mucked. Hole cards for each all-in-revealed seat are captured into
    `pending_all_in_reveals` immediately — not re-read later — because
    `Automation.HAND_KILLING` clears a losing seat's `hole_cards` once a
    winner is determined (see `_build_reveal`'s docstring), which happens
    before `_finalize` gets a chance to build the wire event.
    """
    events: list[Event] = []
    pk = s.pk
    while True:
        if pk.can_deal_board():
            assert pk.street is not None
            count = pk.street.board_dealing_count
            cards_str = cards.draw(s.remaining_deck, count)
            dealt = cards.deal_board(pk, cards_str)
            street = _STREET_ORDER[s.board_deals_done]
            s.board_deals_done += 1
            s.phase = _STREET_TO_PHASE[street]
            events.append(
                _unstamped(EventType.BOARD_DEALT, BoardDealtPayload(street=street, cards=[repr(c) for c in dealt]))
            )
            continue

        if pk.showdown_indices:
            s.hand_had_showdown = True
            seat = pk.showdown_indices[0]
            if pk.all_in_status:
                # No discretion exists (§3.1) — force the reveal. The wire
                # event bundling all-at-once happens later in _finalize,
                # once the final board is known (needed for rank/description);
                # the hole cards themselves are captured right now, before
                # hand-killing can clear them.
                pk.show_or_muck_hole_cards(True, seat)
                s.pending_all_in_reveals[seat] = [repr(c) for c in pk.hole_cards[seat]]
                continue
            s.phase = Phase.SHOWDOWN
            s.awaiting_showdown_seat = seat
            events.append(_unstamped(EventType.ACTION_REQUIRED, ActionRequiredPayload(seat=seat, deadline_ms=0)))
            break

        if pk.can_push_chips():
            events.extend(_finalize(s))
            break

        if pk.actor_index is not None:
            events.append(
                _unstamped(EventType.ACTION_REQUIRED, ActionRequiredPayload(seat=pk.actor_index, deadline_ms=0))
            )
            break

        break  # nothing pending, no actor — safety net against an infinite loop

    return events


def _finalize(s: GameState) -> list[Event]:
    pk = s.pk
    events: list[Event] = []

    if s.pending_all_in_reveals:
        board = _board(pk)
        reveals: list[Reveal] = []
        for seat, hole in s.pending_all_in_reveals.items():
            reveal = _build_reveal(seat, hole, board)
            s.revealed[seat] = reveal
            reveals.append(reveal)
        events.append(_unstamped(EventType.SHOWDOWN, ShowdownPayload(reveals=reveals)))
        s.pending_all_in_reveals = {}

    # Snapshot pot AMOUNTS (plain ints) before pushing — not the Pot objects
    # themselves. `Pot` is a mutable dataclass and push_chips() decrements
    # `pot.unraked_amount` on the SAME object in place as it distributes
    # each sub-pot (verified against pokerkit source, not assumed); holding
    # a tuple of the objects still lets push_chips() corrupt the snapshot
    # out from under it, since a tuple only freezes which objects it holds,
    # not their contents. This was caught by a smoke-test run, not by mypy
    # or a type system — see docs/DECISIONS.md.
    pot_amounts_before = [p.amount for p in tuple(pk.pots)]

    if s.hand_had_showdown:
        per_pot_awards: dict[int, list[Award]] = {i: [] for i in range(len(pot_amounts_before))}
        while pk.can_push_chips():
            op = pk.push_chips()
            for seat, amount in enumerate(op.amounts):
                if amount:
                    per_pot_awards[op.pot_index].append(Award(seat=seat, amount=amount))
        while pk.can_pull_chips():
            pk.pull_chips()
        pot_awards = [
            PotAward(index=i, amount=pot_amounts_before[i], awards=per_pot_awards[i], reason=PotAwardReason.SHOWDOWN)
            for i in range(len(pot_amounts_before))
        ]
    else:
        # Uncontested win (§3.0): there is exactly one live seat left and
        # exactly one pot, so no real pot-splitting is needed — but
        # push_chips()'s `amounts` for this single-eligible-player case
        # reports only the *net transfer*, silently excluding the winner's
        # own already-uncollected bet (their bet just sits and later gets
        # swept into their stack by pull_chips instead of being "pushed").
        # That undercounts the pot relative to what pot_total showed all
        # hand, so the award is computed directly here instead of trusting
        # push_chips().amounts for this one degenerate case. Verified
        # against pokerkit source, not assumed — see docs/DECISIONS.md.
        winner = next(i for i in range(s.seats_total) if s.seat_status[i] != SeatStatus.FOLDED)
        total = sum(pot_amounts_before) + sum(pk.bets)
        while pk.can_push_chips():
            pk.push_chips()
        while pk.can_pull_chips():
            pk.pull_chips()
        pot_awards = (
            [PotAward(index=0, amount=total, awards=[Award(seat=winner, amount=total)], reason=PotAwardReason.UNCONTESTED)]
            if total
            else []
        )
    events.append(_unstamped(EventType.POT_AWARDED, PotAwardedPayload(pots=pot_awards)))
    events.append(
        _unstamped(
            EventType.HAND_COMPLETE,
            HandCompletePayload(hand_no=s.hand_no, stacks=list(pk.stacks), deck=s.full_deck),
        )
    )
    s.phase = Phase.HAND_COMPLETE
    s.awaiting_showdown_seat = None
    return events


def _render_text(s: GameState, seat: int, board: list[str], to_call: int | None) -> str:
    hole = " ".join(repr(c) for c in s.pk.hole_cards[seat])
    board_text = " ".join(board) if board else "(none)"
    call_text = f"To call: {to_call}" if to_call else "Nothing to call"
    return (
        f"You are seat {seat} with {hole}.\n"
        f"Board: {board_text} | Pot: {_pot_total(s.pk)} | {call_text}"
    )


class HoldemAdapter:
    """No-Limit Texas Hold'em, id `"holdem-nl"` (docs/PROTOCOL.md §6)."""

    def __init__(self) -> None:
        self.id = "holdem-nl"
        self.min_players = 2
        self.max_players = 9
        # Structural bounds only — "sb < bb" and "starting_stack >= bb" are
        # cross-field constraints plain JSON Schema cannot express, and the
        # `jsonschema` build in use here has no `$data` support (verified,
        # not assumed). See docs/DECISIONS.md for the room-server
        # integration gap this creates and the defensive check in reset().
        self.config_schema: dict[str, object] = {
            "type": "object",
            "properties": {
                "sb": {"type": "integer", "exclusiveMinimum": 0},
                "bb": {"type": "integer", "exclusiveMinimum": 0},
                "ante": {"type": "integer", "minimum": 0},
                "starting_stack": {"type": "integer", "exclusiveMinimum": 0},
                "turn_seconds": {"type": "integer", "exclusiveMinimum": 0},
            },
            "required": ["sb", "bb", "starting_stack"],
            "additionalProperties": False,
        }

    def reset(self, cfg: dict[str, object], deck: list[str]) -> GameState:
        sb = _int(cfg["sb"])
        bb = _int(cfg["bb"])
        ante = _int(cfg.get("ante", 0))
        starting_stack = _int(cfg["starting_stack"])
        # `_seats` is a room-server-injected reserved key, not part of
        # config_schema — see packages/room_server/store.py and its
        # docs/DECISIONS.md entry on why §9's reset(cfg, deck) needs it.
        seats_total = _int(cfg["_seats"])

        if sb <= 0 or bb <= 0 or ante < 0 or starting_stack <= 0:
            raise ValueError("sb, bb, and starting_stack must be positive; ante must be non-negative")
        if sb >= bb:
            raise ValueError(f"sb ({sb}) must be less than bb ({bb})")
        if starting_stack < bb:
            raise ValueError(f"starting_stack ({starting_stack}) must be at least bb ({bb})")

        remaining_deck = list(deck)
        pk = NoLimitTexasHoldem.create_state(
            _AUTOMATIONS,
            True,
            ante,
            (sb, bb),
            bb,
            tuple([starting_stack] * seats_total),
            seats_total,
            mode=Mode.TOURNAMENT,
        )
        for _ in range(seats_total):
            cards.deal_hole(pk, cards.draw(remaining_deck, 2))

        seat_status = [SeatStatus.ALL_IN if pk.stacks[i] == 0 else SeatStatus.ACTIVE for i in range(seats_total)]

        s = GameState(
            pk=pk,
            hand_no=1,
            # Button is seat (n-1): pokerkit's own indexing convention puts
            # the button there for every player count, heads-up included —
            # verified empirically (heads-up reverses sb/bb by index in a
            # way that still lands the button at index n-1). See
            # docs/DECISIONS.md.
            button=seats_total - 1,
            seats_total=seats_total,
            starting_stack=starting_stack,
            sb=sb,
            bb=bb,
            ante=ante,
            remaining_deck=remaining_deck,
            full_deck=list(deck),
            seat_status=seat_status,
        )
        _advance(s)
        return s

    def setup_events(self, s: GameState) -> list[Event]:
        """Not in §9 — mirrors the interim extension `packages/room_server`
        already added on its side (see its docs/DECISIONS.md entry); this
        confirms the same contract from game-holdem's side so integration
        works today. Flagged for the PROTOCOL.md owner on both sides."""
        postings: list[Posting] = []
        if s.ante > 0:
            postings.extend(Posting(seat=i, amount=s.ante, kind=PostingKind.ANTE) for i in range(s.seats_total))
        if s.seats_total == 2:
            postings.append(Posting(seat=0, amount=s.bb, kind=PostingKind.BB))
            postings.append(Posting(seat=1, amount=s.sb, kind=PostingKind.SB))
        else:
            postings.append(Posting(seat=0, amount=s.sb, kind=PostingKind.SB))
            postings.append(Posting(seat=1, amount=s.bb, kind=PostingKind.BB))

        events = [
            _unstamped(
                EventType.HAND_STARTED,
                HandStartedPayload(hand_no=s.hand_no, button=s.button, stacks=[s.starting_stack] * s.seats_total),
            ),
            _unstamped(EventType.BLINDS_POSTED, BlindsPostedPayload(postings=postings)),
            _unstamped(EventType.HOLE_CARDS_DEALT, HoleCardsDealtPayload(seats=list(range(s.seats_total)))),
        ]

        # A valid config (starting_stack >= bb, sb < bb, >= 2 seats) can
        # never resolve the hand from blinds alone — dealing always leaves
        # at least one seat with a real decision. Asserted, not handled,
        # so a violation fails loudly here instead of silently mis-ordering
        # events.
        assert s.pk.actor_index is not None or s.awaiting_showdown_seat is not None
        seat = s.pk.actor_index if s.pk.actor_index is not None else s.awaiting_showdown_seat
        assert seat is not None
        events.append(_unstamped(EventType.ACTION_REQUIRED, ActionRequiredPayload(seat=seat, deadline_ms=0)))
        return events

    def legal_actions(self, s: GameState, seat: int) -> list[ActionSpec]:
        if s.phase == Phase.HAND_COMPLETE:
            return []
        if s.awaiting_showdown_seat == seat:
            return [ActionSpec(type=ActionType.SHOW), ActionSpec(type=ActionType.MUCK)]
        if s.pk.actor_index != seat:
            return []

        to_call = s.pk.checking_or_calling_amount
        specs: list[ActionSpec] = []
        if to_call:
            specs.append(ActionSpec(type=ActionType.FOLD))
            specs.append(ActionSpec(type=ActionType.CALL, amount=to_call))
        else:
            specs.append(ActionSpec(type=ActionType.CHECK))

        min_to = s.pk.min_completion_betting_or_raising_to_amount
        max_to = s.pk.max_completion_betting_or_raising_to_amount
        if min_to is not None and max_to is not None and max_to >= min_to:
            specs.append(ActionSpec(type=ActionType.RAISE, min_to=min_to, max_to=max_to))
        return specs

    def apply(self, s: GameState, seat: int, a: Action) -> list[Event]:
        if s.phase == Phase.HAND_COMPLETE:
            raise IllegalAction("hand is already complete", [])
        if a.type in (ActionType.SHOW, ActionType.MUCK):
            return self._apply_showdown_decision(s, seat, a)
        return self._apply_betting_decision(s, seat, a)

    def _apply_betting_decision(self, s: GameState, seat: int, a: Action) -> list[Event]:
        legal = self.legal_actions(s, seat)
        if a.type not in {spec.type for spec in legal}:
            raise IllegalAction(f"{a.type.value} is not legal for seat {seat}", legal)
        if a.type == ActionType.RAISE:
            spec = next(sp for sp in legal if sp.type == ActionType.RAISE)
            assert spec.min_to is not None and spec.max_to is not None
            if a.to is None or not (spec.min_to <= a.to <= spec.max_to):
                raise IllegalAction(f"raise to {a.to} is outside [{spec.min_to}, {spec.max_to}]", legal)

        stack_before = s.pk.stacks[seat]
        if a.type == ActionType.FOLD:
            s.pk.fold()
            s.seat_status[seat] = SeatStatus.FOLDED
        elif a.type in (ActionType.CHECK, ActionType.CALL):
            s.pk.check_or_call()
        elif a.type == ActionType.RAISE:
            assert a.to is not None
            s.pk.complete_bet_or_raise_to(a.to)
        else:
            raise IllegalAction(f"unsupported action type {a.type.value}", legal)

        stack_after = s.pk.stacks[seat]
        all_in = a.type != ActionType.FOLD and stack_after == 0
        if all_in:
            s.seat_status[seat] = SeatStatus.ALL_IN
        s.last_action[seat] = a

        events = [
            _unstamped(
                EventType.ACTION_TAKEN,
                ActionTakenPayload(
                    seat=seat,
                    action=a,
                    amount_added=stack_before - stack_after,
                    stack_after=stack_after,
                    pot_after=_pot_total(s.pk),
                    all_in=all_in,
                ),
            )
        ]
        events.extend(_advance(s))
        return events

    def _apply_showdown_decision(self, s: GameState, seat: int, a: Action) -> list[Event]:
        if s.awaiting_showdown_seat != seat:
            raise IllegalAction(f"seat {seat} has no showdown decision pending", [])

        show = a.type == ActionType.SHOW
        s.pk.show_or_muck_hole_cards(show, seat)
        s.awaiting_showdown_seat = None
        s.last_action[seat] = a

        events: list[Event] = []
        if show:
            # Captured now, before _advance() below can trigger hand-killing
            # (see _build_reveal's docstring) if this turns out to be a
            # losing hand.
            hole = [repr(c) for c in s.pk.hole_cards[seat]]
            reveal = _build_reveal(seat, hole, _board(s.pk))
            s.revealed[seat] = reveal
            events.append(_unstamped(EventType.SHOWDOWN, ShowdownPayload(reveals=[reveal])))
        events.extend(_advance(s))
        return events

    def view(self, s: GameState, seat: int) -> Observation:
        pk = s.pk
        pots = tuple(pk.pots)  # materialized ONCE (§10 trap)
        pot_views = [PotView(index=i, amount=p.amount, eligible_seats=list(p.player_indices)) for i, p in enumerate(pots)]
        pot_total = sum(p.amount for p in pots)
        # `pk.pots` only reflects streets already swept by bet collection —
        # the current street's live bets sit in `pk.bets` uncollected until
        # the round closes, so `tuple(pk.pots)` alone under-reports the pot
        # for most of a hand (confirmed empirically: it's `()` for the
        # entire preflop round, even with both blinds posted). A viewer's
        # "total in the middle right now" must include those too.
        live_bets = sum(pk.bets)
        if live_bets:
            live_seats = [i for i in range(s.seats_total) if s.seat_status[i] != SeatStatus.FOLDED]
            pot_views.append(PotView(index=len(pot_views), amount=live_bets, eligible_seats=live_seats))
            pot_total += live_bets

        if s.phase == Phase.HAND_COMPLETE:
            to_act = None
        elif s.awaiting_showdown_seat is not None:
            to_act = s.awaiting_showdown_seat
        else:
            to_act = pk.actor_index

        board = _board(pk)

        seat_views = [
            SeatView(
                seat=i,
                name="",  # overlaid by the room server with join-time metadata
                kind=SeatKind.HUMAN,  # overlaid by the room server
                stack=pk.stacks[i],
                committed_street=pk.bets[i],
                status=s.seat_status[i],
                last_action=s.last_action.get(i),
                revealed=s.revealed.get(i),
            )
            for i in range(s.seats_total)
        ]

        committed_hand = None if s.phase == Phase.HAND_COMPLETE else s.starting_stack - pk.stacks[seat]
        you = YouView(
            seat=seat,
            name="",  # overlaid by the room server
            hole=[repr(c) for c in pk.hole_cards[seat]],
            stack=pk.stacks[seat],
            committed_street=pk.bets[seat],
            committed_hand=committed_hand,
            status=s.seat_status[seat],
        )

        to_call = pk.checking_or_calling_amount
        min_raise_to = pk.min_completion_betting_or_raising_to_amount
        max_raise_to = pk.max_completion_betting_or_raising_to_amount

        return Observation(
            protocol_version="",  # overlaid by the room server
            seq=0,  # overlaid by the room server
            room_id="",  # overlaid by the room server
            hand_no=s.hand_no,
            phase=s.phase,
            to_act=to_act,
            button=s.button,
            you=you,
            board=board,
            pots=pot_views,
            pot_total=pot_total,
            seats=seat_views,
            to_call=to_call,
            min_raise_to=min_raise_to,
            max_raise_to=max_raise_to,
            legal_actions=self.legal_actions(s, seat),
            chat=[],  # overlaid by the room server
            text=_render_text(s, seat, board, to_call),
        )

    def is_terminal(self, s: GameState) -> bool:
        return s.phase == Phase.HAND_COMPLETE

    def results(self, s: GameState) -> dict[int, float]:
        return {i: float(s.pk.stacks[i] - s.starting_stack) for i in range(s.seats_total)}
