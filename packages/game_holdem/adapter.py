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

from pokerkit import Automation, Mode, NoLimitTexasHoldem, Pot, StandardHighHand, State

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
    SeatJoinedPayload,
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
    starting_stacks: list[int]
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
    # Pre-drain snapshot, populated by _finalize before any push_chips()
    # call — see the `pots` property below.
    _final_pots: tuple[Pot, ...] | None = field(default=None, repr=False)

    @property
    def pots(self) -> tuple[Pot, ...]:
        """Overrides `__getattr__` delegation deliberately: `pk.pots` is
        drained (mutated to `0`) by `push_chips()` once the hand resolves
        (docs/DECISIONS.md, "three real bugs found only by driving real
        hands" — Bug 1), so reading it post-hoc — exactly what a caller
        checking pot structure *after* a hand completes would naturally do
        — always sees zeroed amounts, not what was actually paid out. This
        returns the last pre-drain snapshot once one exists (captured in
        `_finalize`, before any `push_chips()` call) and falls through to
        the live pokerkit value otherwise (mid-hand, where nothing has
        drained yet)."""
        if self._final_pots is not None:
            return self._final_pots
        return tuple(self.pk.pots)

    @property
    def runout_count(self) -> int:
        """Overrides `__getattr__` delegation deliberately: pokerkit's own
        `state.runout_count` stays `None` for the entire hand in
        `Mode.TOURNAMENT` — no runout-count selection ever happens there,
        so it's never explicitly set (docs/DECISIONS.md,
        "Automation.RUNOUT_COUNT_SELECTION is unnecessary, not just
        unused"). `None` reads as "unset, check elsewhere" rather than
        "guaranteed to behave as 1"; this reports the actual guarantee
        instead of leaving a caller to infer it from a missing value."""
        value = self.pk.runout_count
        return value if value is not None else 1

    def __getattr__(self, name: str) -> object:
        """Transparent read-through to the wrapped `pokerkit.State` for any
        attribute `GameState` doesn't define itself.

        §10's derivation table is written entirely in terms of `state.X`
        (`state.hole_cards[i]`, `state.pots`, `state.bets[i]`,
        `state.can_win_now(seat)`, ...), matching raw pokerkit attribute
        names directly — the natural reading is that whatever
        `GameAdapter.reset()` returns supports that same access pattern.
        But §10 also requires bookkeeping pokerkit doesn't do on its own
        ("maintain our own per-seat status alongside PokerKit state rather
        than deriving it fresh each view"), which is exactly why
        `GameState` exists as a wrapper instead of `reset()` just handing
        back the bare `State`. This delegation makes both true at once:
        `GameState`'s own fields (seat_status, phase, ...) take priority as
        normal attributes, and anything it doesn't define falls through to
        `self.pk` unchanged. Only triggered for names normal attribute
        lookup doesn't find — `GameState`'s own dataclass fields are never
        shadowed by this. See docs/DECISIONS.md."""
        return getattr(self.pk, name)


def _int(value: object) -> int:
    assert isinstance(value, int)
    return value


def _check_cross_field_config(sb: int, bb: int, ante: int) -> None:
    """The cross-field constraints `config_schema` (plain JSON Schema)
    cannot express — no `sb`/`bb` comparison keyword exists in the schema
    draft this package validates against, and the `jsonschema` build here
    has no `$data` extension (verified, not assumed). Shared between
    `validate_config` (the real gate, called at room creation) and `reset`
    (defensive only — see its call site)."""
    if sb <= 0 or bb <= 0 or ante < 0:
        raise ValueError("sb and bb must be positive; ante must be non-negative")
    if sb >= bb:
        raise ValueError(f"sb ({sb}) must be less than bb ({bb})")


def _resolve_starting_stacks(cfg: dict[str, object], bb: int, seats: int) -> list[int]:
    """§6: exactly one of `starting_stack` / `starting_stacks` must be
    present. `starting_stacks` (docs/DECISIONS.md, "starting_stacks:
    per-seat stacks in room config") exists because equal stacks make
    tiered all-ins mathematically unreachable within a single hand — every
    `call` matches the current bet exactly, so live seats always hold
    identical capacity. Shared between `validate_config` (the real gate)
    and `reset` (defensive only — see its call site)."""
    has_single = "starting_stack" in cfg
    has_list = "starting_stacks" in cfg
    if has_single == has_list:
        raise ValueError("exactly one of starting_stack or starting_stacks must be present")
    if has_single:
        starting_stack = _int(cfg["starting_stack"])
        if starting_stack < bb:
            raise ValueError(f"starting_stack ({starting_stack}) must be at least bb ({bb})")
        return [starting_stack] * seats

    raw = cfg["starting_stacks"]
    if not isinstance(raw, list) or len(raw) != seats:
        raise ValueError(f"starting_stacks must be a list of length {seats} (one entry per seat)")
    stacks: list[int] = []
    for entry in raw:
        if not isinstance(entry, int) or isinstance(entry, bool) or entry < bb:
            raise ValueError(f"every starting_stacks entry must be an integer >= bb ({bb}); got {entry!r}")
        stacks.append(entry)
    return stacks


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
    raw_pots = tuple(pk.pots)
    pot_amounts_before = [p.amount for p in raw_pots]
    # Also freeze independent copies for the `pots` property (above) to
    # serve post-hoc — same reasoning, decoupled from the objects
    # push_chips() is about to mutate.
    s._final_pots = tuple(Pot(p.raked_amount, p.unraked_amount, p.player_indices) for p in raw_pots)

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
        # Structural bounds only — "sb < bb", "starting_stack >= bb", and
        # "exactly one of starting_stack/starting_stacks" are cross-field
        # constraints plain JSON Schema cannot express, and the `jsonschema`
        # build in use here has no `$data` support (verified, not assumed).
        # See docs/DECISIONS.md for the room-server integration gap this
        # creates and the defensive check in reset().
        self.config_schema: dict[str, object] = {
            "type": "object",
            "properties": {
                "sb": {"type": "integer", "exclusiveMinimum": 0},
                "bb": {"type": "integer", "exclusiveMinimum": 0},
                "ante": {"type": "integer", "minimum": 0},
                "starting_stack": {"type": "integer", "exclusiveMinimum": 0},
                "starting_stacks": {"type": "array", "items": {"type": "integer"}},
                "turn_seconds": {"type": "integer", "exclusiveMinimum": 0},
            },
            "required": ["sb", "bb"],
            "additionalProperties": False,
        }

    def validate_config(self, cfg: dict[str, object], seats: int) -> None:
        """Not in §9 — called by the room server at `POST /rooms`, before a
        room is created, so a config that fails only this (not
        `config_schema`) still gets `400 invalid_config` at creation time
        instead of crashing `/start` later. See docs/DECISIONS.md, "a bad
        config could crash /start instead of failing at POST /rooms"."""
        sb = _int(cfg["sb"])
        bb = _int(cfg["bb"])
        ante = _int(cfg.get("ante", 0))
        _check_cross_field_config(sb, bb, ante)
        _resolve_starting_stacks(cfg, bb, seats)

    def reset(self, cfg: dict[str, object], deck: list[str]) -> GameState:
        sb = _int(cfg["sb"])
        bb = _int(cfg["bb"])
        ante = _int(cfg.get("ante", 0))
        # `_seats` is a room-server-injected reserved key, not part of
        # config_schema — see packages/room_server/store.py and its
        # docs/DECISIONS.md entry on why §9's reset(cfg, deck) needs it.
        seats_total = _int(cfg["_seats"])
        # Defensive, not the primary gate: `validate_config` above is what
        # the room server calls at POST /rooms time. This repeats the same
        # checks so a caller that invokes reset() directly (as several of
        # this package's own unit tests deliberately do) still gets a clear
        # error instead of a confusing failure deeper in pokerkit.
        _check_cross_field_config(sb, bb, ante)
        starting_stacks = _resolve_starting_stacks(cfg, bb, seats_total)

        remaining_deck = list(deck)
        pk = NoLimitTexasHoldem.create_state(
            _AUTOMATIONS,
            True,
            ante,
            (sb, bb),
            bb,
            tuple(starting_stacks),
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
            starting_stacks=starting_stacks,
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
                HandStartedPayload(hand_no=s.hand_no, button=s.button, stacks=list(s.starting_stacks)),
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
        # Captured BEFORE show_or_muck_hole_cards, not after: for the LAST
        # seat with a pending showdown decision, that call itself cascades
        # synchronously all the way through pokerkit's internal
        # _end_showdown -> _begin_hand_killing chain (HAND_KILLING is an
        # enabled automation) before it returns control here — the same
        # class of bug _build_reveal's docstring documents for the all-in
        # path, just reachable one call earlier than it looks. Verified by
        # an end-to-end run: seat 3 of 4 (the last to show) came back with
        # hole=[] until this line moved above the call. Regression test:
        # test_last_to_show_in_a_3way_discretionary_showdown_has_nonempty_hole_cards_even_when_losing.
        # See docs/DECISIONS.md.
        hole = [repr(c) for c in s.pk.hole_cards[seat]]
        s.pk.show_or_muck_hole_cards(show, seat)
        s.awaiting_showdown_seat = None
        s.last_action[seat] = a

        events: list[Event] = []
        if show:
            reveal = _build_reveal(seat, hole, _board(s.pk))
            s.revealed[seat] = reveal
            events.append(_unstamped(EventType.SHOWDOWN, ShowdownPayload(reveals=[reveal])))
        events.extend(_advance(s))
        return events

    def view(self, s: GameState, seat: int) -> Observation:
        pk = s.pk
        pots = tuple(pk.pots)  # materialized ONCE (§10 trap)
        pot_views = [PotView(index=i, amount=p.amount, eligible_seats=list(p.player_indices)) for i, p in enumerate(pots)]
        # `pk.pots` only reflects streets already swept by bet collection —
        # the current street's live bets sit in `pk.bets` uncollected until
        # the round closes, so `tuple(pk.pots)` alone under-reports the pot
        # for most of a hand (confirmed empirically: it's `()` for the entire
        # preflop round, even with both blinds posted). Those live bets go
        # into `pot_total` only, not `pots[]`: `pots[].index` must stay
        # stable for `pot_awarded` (§5) to reference, and a live bet is not
        # yet a settled pot — it hasn't been divided into tiers, so folding
        # it into `pots[]` would shift every later index once it resolves.
        # See docs/DECISIONS.md.
        pot_total = sum(p.amount for p in pots) + sum(pk.bets)

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

        committed_hand = None if s.phase == Phase.HAND_COMPLETE else s.starting_stacks[seat] - pk.stacks[seat]
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

    def is_terminal(self, s: GameState) -> bool:
        return s.phase == Phase.HAND_COMPLETE

    def results(self, s: GameState) -> dict[int, float]:
        return {i: float(s.pk.stacks[i] - s.starting_stacks[i]) for i in range(s.seats_total)}
