"""Unit tests for packages/game_holdem — driven directly against
HoldemAdapter, no HTTP. These are game-holdem's own scratchpad (AGENTS.md);
tests/contract/ is the frozen spec and is not touched here.
"""

from __future__ import annotations

from pokerkit import Card

from packages.engine.types import (
    Action,
    ActionType,
    EventType,
    Phase,
    PotAwardReason,
    SeatStatus,
)
from packages.game_holdem.adapter import HoldemAdapter

RANKS = "23456789TJQKA"
SUITS = "cdhs"


def _full_deck() -> list[str]:
    return [r + s for s in SUITS for r in RANKS]


def _rigged_deck(*groups: str) -> list[str]:
    """A 52-card deck starting with the given 2-char card groups in order
    (hole cards first, then board), followed by the rest of a standard deck
    in a fixed order. Lets a test pin down exactly who wins."""
    used: list[str] = []
    for g in groups:
        used.extend(g[i : i + 2] for i in range(0, len(g), 2))
    rest = [c for c in _full_deck() if c not in used]
    return used + rest


def _cfg(seats: int, **overrides: object) -> dict[str, object]:
    base: dict[str, object] = {"sb": 25, "bb": 50, "ante": 0, "starting_stack": 5000, "_seats": seats}
    base.update(overrides)
    if "starting_stacks" in overrides:
        # §6: exactly one of starting_stack / starting_stacks may be present.
        base.pop("starting_stack", None)
    return base


def _to_act(adapter: HoldemAdapter, s: object) -> int | None:
    return adapter.view(s, 0).to_act


def _shove_everyone(adapter: HoldemAdapter, s: object, seats: int) -> list[object]:
    """Drive every seat to raise-to-max or call until no one has a betting
    decision left."""
    events: list[object] = []
    for _ in range(seats * 4):
        seat = _to_act(adapter, s)
        if seat is None:
            break
        legal = adapter.legal_actions(s, seat)
        raise_spec = next((a for a in legal if a.type == ActionType.RAISE), None)
        if raise_spec is not None and raise_spec.max_to is not None:
            events = adapter.apply(s, seat, Action(type=ActionType.RAISE, to=raise_spec.max_to))
        else:
            events = adapter.apply(s, seat, Action(type=ActionType.CALL))
    return events


def _check_to_showdown(adapter: HoldemAdapter, s: object, seats: int) -> None:
    for _ in range(seats * 8):
        seat = _to_act(adapter, s)
        if seat is None or adapter.is_terminal(s):
            return
        legal = adapter.legal_actions(s, seat)
        types = {a.type for a in legal}
        if ActionType.CHECK in types:
            adapter.apply(s, seat, Action(type=ActionType.CHECK))
        elif ActionType.CALL in types:
            adapter.apply(s, seat, Action(type=ActionType.CALL))
        else:
            return


# -- dealing -----------------------------------------------------------------


def test_hole_cards_are_dealt_as_card_instances() -> None:
    adapter = HoldemAdapter()
    s = adapter.reset(_cfg(3), _full_deck())
    for seat in range(3):
        assert len(s.pk.hole_cards[seat]) == 2
        assert all(isinstance(c, Card) for c in s.pk.hole_cards[seat])


def test_reset_dealing_is_a_pure_function_of_the_deck() -> None:
    """Two independent reset() calls from the identical deck, no actions
    applied to either, deal byte-identical hole cards — dealing is fixed
    the moment reset() returns."""
    adapter = HoldemAdapter()
    deck = _full_deck()
    a = adapter.reset(_cfg(3), list(deck))
    b = adapter.reset(_cfg(3), list(deck))
    for seat in range(3):
        assert [repr(c) for c in a.pk.hole_cards[seat]] == [repr(c) for c in b.pk.hole_cards[seat]]


# -- betting -------------------------------------------------------------------


def test_raise_to_moves_stack_by_delta_not_by_the_raw_amount() -> None:
    adapter = HoldemAdapter()
    s = adapter.reset(_cfg(2), _full_deck())
    seat = _to_act(adapter, s)
    assert seat is not None
    legal = adapter.legal_actions(s, seat)
    raise_spec = next(a for a in legal if a.type == ActionType.RAISE)
    assert raise_spec.min_to is not None
    stack_before = s.pk.stacks[seat]
    bet_before = s.pk.bets[seat]
    adapter.apply(s, seat, Action(type=ActionType.RAISE, to=raise_spec.min_to))
    assert stack_before - s.pk.stacks[seat] == raise_spec.min_to - bet_before


def test_max_raise_to_is_stack_plus_current_bet() -> None:
    adapter = HoldemAdapter()
    s = adapter.reset(_cfg(2), _full_deck())
    seat = _to_act(adapter, s)
    assert seat is not None
    raise_spec = next(a for a in adapter.legal_actions(s, seat) if a.type == ActionType.RAISE)
    assert raise_spec.max_to == s.pk.stacks[seat] + s.pk.bets[seat]


def test_short_stack_all_in_raise_is_offered_when_min_equals_max() -> None:
    adapter = HoldemAdapter()
    s = adapter.reset(_cfg(2, starting_stack=55), _full_deck())
    seat = _to_act(adapter, s)
    assert seat is not None
    raise_specs = [a for a in adapter.legal_actions(s, seat) if a.type == ActionType.RAISE]
    assert raise_specs
    spec = raise_specs[0]
    assert spec.min_to == spec.max_to == s.pk.stacks[seat] + s.pk.bets[seat]


def test_no_raise_offered_once_every_other_live_seat_is_all_in() -> None:
    """§3: PokerKit returns None, not a small integer, when raising isn't
    legal — this exercises the real trap: comparing None <= None without a
    None check first raises TypeError.

    With a uniform starting_stack, every seat's total capacity for the hand
    is identical (stack + already-committed bets always equals
    starting_stack, until someone actually loses chips) — so the very first
    shove already caps every other seat at the same maximum, and nobody
    after that first shove can ever raise, only call. One shove is enough."""
    adapter = HoldemAdapter()
    s = adapter.reset(_cfg(3, starting_stack=500), _full_deck())
    seat = _to_act(adapter, s)
    assert seat is not None
    raise_spec = next(a for a in adapter.legal_actions(s, seat) if a.type == ActionType.RAISE)
    assert raise_spec.max_to is not None
    adapter.apply(s, seat, Action(type=ActionType.RAISE, to=raise_spec.max_to))

    next_seat = _to_act(adapter, s)
    assert next_seat is not None
    legal = adapter.legal_actions(s, next_seat)  # must not raise TypeError
    assert not any(a.type == ActionType.RAISE for a in legal)


def test_fold_when_facing_no_bet_is_never_offered() -> None:
    adapter = HoldemAdapter()
    s = adapter.reset(_cfg(2), _full_deck())
    seat = _to_act(adapter, s)
    assert seat is not None
    adapter.apply(s, seat, Action(type=ActionType.CALL))
    next_seat = _to_act(adapter, s)
    assert next_seat is not None
    legal = adapter.legal_actions(s, next_seat)
    types = {a.type for a in legal}
    assert ActionType.CHECK in types
    assert ActionType.FOLD not in types


# -- seat status / derived fields ----------------------------------------------


def test_seat_status_across_fold_active_and_all_in() -> None:
    adapter = HoldemAdapter()
    s = adapter.reset(_cfg(3), _full_deck())

    folded = _to_act(adapter, s)
    assert folded is not None
    adapter.apply(s, folded, Action(type=ActionType.FOLD))
    assert adapter.view(s, folded).you.status == SeatStatus.FOLDED

    all_in_seat = _to_act(adapter, s)
    assert all_in_seat is not None
    still_active_seat = next(i for i in range(3) if i not in (folded, all_in_seat))
    assert adapter.view(s, still_active_seat).you.status == SeatStatus.ACTIVE

    raise_spec = next(a for a in adapter.legal_actions(s, all_in_seat) if a.type == ActionType.RAISE)
    assert raise_spec.max_to is not None
    adapter.apply(s, all_in_seat, Action(type=ActionType.RAISE, to=raise_spec.max_to))
    assert adapter.view(s, all_in_seat).you.status == SeatStatus.ALL_IN


def test_committed_hand_is_none_once_hand_complete() -> None:
    adapter = HoldemAdapter()
    s = adapter.reset(_cfg(2), _full_deck())
    seat = _to_act(adapter, s)
    assert seat is not None
    adapter.apply(s, seat, Action(type=ActionType.FOLD))
    assert adapter.is_terminal(s)
    for viewer in range(2):
        assert adapter.view(s, viewer).you.committed_hand is None


def test_pot_total_reflects_uncollected_bets_mid_street() -> None:
    """`state.pots` alone is `()` for an entire street until bet collection
    closes it — pot_total must still show the blinds immediately."""
    adapter = HoldemAdapter()
    s = adapter.reset(_cfg(2), _full_deck())  # sb=25, bb=50
    obs = adapter.view(s, 0)
    assert obs.pot_total == 75


# -- uncontested / showdown mechanics -------------------------------------------


def test_uncontested_fold_awards_full_pot_with_no_showdown_event() -> None:
    adapter = HoldemAdapter()
    s = adapter.reset(_cfg(2), _full_deck())
    seat = _to_act(adapter, s)
    assert seat is not None
    events = adapter.apply(s, seat, Action(type=ActionType.FOLD))
    assert not any(e.type == EventType.SHOWDOWN for e in events)
    pot_awarded = next(e for e in events if e.type == EventType.POT_AWARDED)
    assert all(p.reason == PotAwardReason.UNCONTESTED for p in pot_awarded.payload.pots)
    assert sum(a.amount for p in pot_awarded.payload.pots for a in p.awards) == sum(
        p.amount for p in pot_awarded.payload.pots
    )
    # the winner receives the FULL pot (both blinds), not pokerkit's own
    # narrower "net transfer" accounting — see docs/DECISIONS.md.
    assert pot_awarded.payload.pots[0].amount == 75


def test_showdown_with_one_non_all_in_seat_offers_show_and_muck() -> None:
    adapter = HoldemAdapter()
    s = adapter.reset(_cfg(2, starting_stack=5000), _full_deck())
    _check_to_showdown(adapter, s, 2)
    seat = _to_act(adapter, s)
    assert seat is not None
    assert adapter.view(s, seat).phase == Phase.SHOWDOWN
    types = {a.type for a in adapter.legal_actions(s, seat)}
    assert types == {ActionType.SHOW, ActionType.MUCK}


def test_known_winner_shows_and_takes_the_pot_loser_can_muck() -> None:
    """Rigged deck: seat 0 gets pocket aces, seat 1 pocket deuces, board
    has nothing that helps seat 1. Seat 0 shows (wins), seat 1 mucks —
    mucking must exclude them from awards and from the public reveal."""
    adapter = HoldemAdapter()
    deck = _rigged_deck("AhAd", "2c2d", "3s7d9hJc4h")
    s = adapter.reset(_cfg(2, starting_stack=5000), deck)
    _check_to_showdown(adapter, s, 2)

    all_events: list[object] = []
    for _ in range(4):
        seat = _to_act(adapter, s)
        if seat is None:
            break
        action = Action(type=ActionType.SHOW) if seat == 0 else Action(type=ActionType.MUCK)
        all_events.extend(adapter.apply(s, seat, action))

    assert adapter.is_terminal(s)
    showdown_events = [e for e in all_events if e.type == EventType.SHOWDOWN]
    revealed_seats = {r.seat for e in showdown_events for r in e.payload.reveals}
    assert revealed_seats == {0}, "the mucking seat must not appear in any showdown reveal"

    pot_awarded = next(e for e in all_events if e.type == EventType.POT_AWARDED)
    awarded_seats = {a.seat for p in pot_awarded.payload.pots for a in p.awards}
    assert awarded_seats == {0}, "a mucked hand forfeits pot eligibility even without being evaluated here"

    obs = adapter.view(s, 1)
    assert obs.seats[1].revealed is None


def test_last_to_show_in_a_3way_discretionary_showdown_has_nonempty_hole_cards_even_when_losing() -> None:
    """Regression test for a real bug (docs/DECISIONS.md, "a real
    game-holdem bug found via play_hand.sh"): for the LAST seat with a
    pending showdown decision, show_or_muck_hole_cards() itself cascades
    synchronously through pokerkit's internal hand-killing (which clears a
    losing hand's hole cards) before returning. Capturing hole cards
    *after* that call instead of *before* silently produced an empty
    `hole` for whichever seat happened to decide last, if they lost.

    This needs the last decider to actually *lose* — with the unrigged
    deck every seat ties on the board's own straight flush (`can_win_now`
    is true for all of them, so hand-killing never fires), which is
    exactly why this went undetected: an earlier version of this test used
    an unrigged deck and kept passing even with the bug reintroduced.
    Rigged so seat 2 (empirically the last to decide in this 3-seat,
    button-last setup) holds the clear loser."""
    adapter = HoldemAdapter()
    deck = _rigged_deck("AhAd", "KsKc", "2c3d", "9h4s7cJd6h")
    s = adapter.reset(_cfg(3, starting_stack=5000), deck)
    _check_to_showdown(adapter, s, 3)

    all_events: list[object] = []
    decision_order: list[int] = []
    for _ in range(4):
        seat = _to_act(adapter, s)
        if seat is None:
            break
        decision_order.append(seat)
        all_events.extend(adapter.apply(s, seat, Action(type=ActionType.SHOW)))

    assert adapter.is_terminal(s)
    assert decision_order[-1] == 2, "this test only proves the fix if seat 2 (the rigged loser) decides last"
    showdown_events = [e for e in all_events if e.type == EventType.SHOWDOWN]
    assert len(showdown_events) == 3, "one individual showdown event per discretionary show (no all-in here)"
    revealed = {r.seat: r.hole for e in showdown_events for r in e.payload.reveals}
    assert set(revealed) == {0, 1, 2}
    for seat, hole in revealed.items():
        assert hole, f"seat {seat}'s revealed hole cards must never be empty"
        assert len(hole) == 2
    results = adapter.results(s)
    assert results[2] < 0, "seat 2 must actually be the loser for this test to exercise the hand-killing path"


def test_known_winner_all_in_preflop_still_gets_correct_award() -> None:
    """Same rigged hand, but both shove preflop — no discretion exists
    (§3.1), so both get force-shown, and the pot still goes to aces."""
    adapter = HoldemAdapter()
    deck = _rigged_deck("AhAd", "2c2d", "3s7d9hJc4h")
    s = adapter.reset(_cfg(2, starting_stack=500), deck)
    _shove_everyone(adapter, s, 2)
    assert adapter.is_terminal(s)
    results = adapter.results(s)
    assert results[0] > 0
    assert results[1] < 0


def test_3way_preflop_all_in_deals_full_board_and_exposes_every_live_hand() -> None:
    """THE unresolved question from §10, settled by test rather than by
    reasoning: does pokerkit auto-resolve an all-in showdown without
    HOLE_CARDS_SHOWING_OR_MUCKING automated? Empirically: no — nothing
    happens until showdown_indices is driven explicitly (verified directly
    against pokerkit 0.7.4, see docs/DECISIONS.md). This test is the
    guardrail: it fails immediately if that ever changes, because the
    adapter's explicit loop would then be racing pokerkit's own automatic
    resolution instead of driving an otherwise-idle state machine."""
    adapter = HoldemAdapter()
    s = adapter.reset(_cfg(3, starting_stack=150), _full_deck())
    events = _shove_everyone(adapter, s, 3)

    assert adapter.is_terminal(s)
    assert len(adapter.view(s, 0).board) == 5

    showdown_events = [e for e in events if e.type == EventType.SHOWDOWN]
    assert len(showdown_events) == 1, "no discretion existed — exactly one bundled showdown event"
    revealed_seats = {r.seat for r in showdown_events[0].payload.reveals}
    assert revealed_seats == {0, 1, 2}


def test_awards_sum_exactly_to_pot_amount_across_many_hands() -> None:
    adapter = HoldemAdapter()
    for variant in range(10):
        deck = _full_deck()
        # rotate the deck deterministically per variant instead of using a
        # real RNG — this file only needs varied-but-reproducible decks,
        # not cryptographic shuffling.
        deck = deck[variant:] + deck[:variant]
        s = adapter.reset(_cfg(3, starting_stack=150 + variant * 7), deck)
        events = _shove_everyone(adapter, s, 3)
        pot_awarded = next(e for e in events if e.type == EventType.POT_AWARDED)
        for pot in pot_awarded.payload.pots:
            assert sum(a.amount for a in pot.awards) == pot.amount
        results = adapter.results(s)
        assert abs(sum(results.values())) < 1e-9


def test_3way_unequal_stacks_produce_more_than_one_pot_with_correct_eligibility() -> None:
    """`pk.pots` is drained by push_chips() by the time the hand is
    terminal (see docs/DECISIONS.md), so this asserts on the wire event's
    pot count, captured before that happens.

    Uses `config.starting_stacks` (docs/PROTOCOL.md §6, docs/DECISIONS.md
    "starting_stacks: per-seat stacks in room config") to reach unequal
    stacks through `reset()` directly rather than hand-building a
    `GameState` — with a uniform `starting_stack`, every seat's total hand
    capacity is forced equal (stack + already-committed bets == the same
    starting_stack for everyone until someone actually loses chips), so a
    genuine side pot is mathematically unreachable in any single M1 hand,
    which is exactly the gap `starting_stacks` closes."""
    adapter = HoldemAdapter()
    s = adapter.reset(_cfg(3, starting_stacks=[100, 300, 300]), _full_deck())
    events = _shove_everyone(adapter, s, 3)
    pot_awarded = next(e for e in events if e.type == EventType.POT_AWARDED)
    assert len(pot_awarded.payload.pots) > 1, "unequal all-in stacks should split into more than one pot tier"
    main_pot = pot_awarded.payload.pots[0]
    assert {a.seat for a in main_pot.awards} <= {0, 1, 2}
    side_pot = pot_awarded.payload.pots[-1]
    assert 0 not in {a.seat for a in side_pot.awards}, "the short stack (seat 0) must be excluded from the side pot"
    for pot in pot_awarded.payload.pots:
        assert sum(a.amount for a in pot.awards) == pot.amount


# -- config validation -----------------------------------------------------------


def test_reset_rejects_sb_greater_or_equal_to_bb() -> None:
    adapter = HoldemAdapter()
    try:
        adapter.reset(_cfg(2, sb=50, bb=25), _full_deck())
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "sb" in str(exc)


def test_reset_rejects_starting_stack_below_bb() -> None:
    adapter = HoldemAdapter()
    try:
        adapter.reset(_cfg(2, starting_stack=10), _full_deck())
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "starting_stack" in str(exc)


# -- forced-off features (§10) ----------------------------------------------------


def test_rake_is_always_zero() -> None:
    adapter = HoldemAdapter()
    s = adapter.reset(_cfg(2), _full_deck())
    for pot in s.pk.pots:
        assert pot.raked_amount == 0


def test_runout_count_is_never_more_than_one() -> None:
    """Tournament mode (used unconditionally) never offers runout-count
    selection at all — verified, not assumed. §10 forces this to 1."""
    adapter = HoldemAdapter()
    s = adapter.reset(_cfg(3, starting_stack=150), _full_deck())
    _shove_everyone(adapter, s, 3)
    assert s.pk.runout_count in (None, 1)
