"""§3/§5/§10 showdown, side-pot, and seat-status contract tests, driven
directly against `GameAdapter`. Every test fails at collection today — see
conftest.py and test_adapter_dealing.py's module docstring.
"""

from __future__ import annotations

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


def _deck() -> list[str]:
    return [r + s for s in SUITS for r in RANKS]


def _cfg(seats: int, **overrides: object) -> dict[str, object]:
    base: dict[str, object] = {"sb": 25, "bb": 50, "ante": 0, "starting_stack": 500, "_seats": seats}
    base.update(overrides)
    if "starting_stacks" in overrides:
        # §6: exactly one of starting_stack / starting_stacks may be present.
        base.pop("starting_stack", None)
    return base


def _to_act(adapter: HoldemAdapter, state: object, any_seat: int = 0) -> int | None:
    return adapter.view(state, any_seat).to_act


def _shove_everyone(adapter: HoldemAdapter, state: object, seats: int) -> list[object]:
    """Drive every seat to raise-to-max or call, until nobody has a decision
    left. Returns the events from the final action that closed out
    decisions (which, with no discretion left, should carry the whole
    board-out/showdown/pot_award/hand_complete suffix per §5.0)."""
    events: list[object] = []
    for _ in range(seats * 4):
        seat = _to_act(adapter, state)
        if seat is None:
            break
        legal = adapter.legal_actions(state, seat)
        raise_spec = next((a for a in legal if a.type == ActionType.RAISE), None)
        if raise_spec is not None and raise_spec.max_to is not None:
            events = adapter.apply(state, seat, Action(type=ActionType.RAISE, to=raise_spec.max_to))
        else:
            events = adapter.apply(state, seat, Action(type=ActionType.CALL))
    return events


def _check_down_to_showdown(adapter: HoldemAdapter, state: object, seats: int) -> None:
    """Drive a hand with everyone checking/calling the minimum, staying
    short of all-in, until a showdown decision or hand_complete is
    reached."""
    for _ in range(seats * 8):
        seat = _to_act(adapter, state)
        if seat is None or adapter.is_terminal(state):
            return
        legal = adapter.legal_actions(state, seat)
        types = {a.type for a in legal}
        if ActionType.CHECK in types:
            adapter.apply(state, seat, Action(type=ActionType.CHECK))
        elif ActionType.CALL in types:
            adapter.apply(state, seat, Action(type=ActionType.CALL))
        elif ActionType.SHOW in types:
            return  # reached the showdown decision itself — caller handles it
        else:
            adapter.apply(state, seat, Action(type=ActionType.FOLD))


def test_3way_all_in_unequal_stacks_produces_multiple_pots_with_correct_eligibility() -> None:
    """§10 checklist: "3-way all-in with unequal stacks produces >1 entry in
    pots[] with correct eligible_seats." A uniform `starting_stack` cannot
    reach this scenario: within one hand, every `call` matches the current
    bet exactly, so any two live seats always hold identical capacity —
    three real stack tiers are mathematically unreachable without unequal
    starting stacks. `config.starting_stacks` (docs/PROTOCOL.md §6,
    docs/DECISIONS.md "starting_stacks: per-seat stacks in room config")
    exists for exactly this. Verified against `pokerkit`'s pre-split
    `state.pots` (§10: "side pots arrive pre-split, no remainder math
    required"; PROTOCOL_REVIEW finding #2 in docs/DECISIONS.md).

    With stacks 100/300/600, seat 0's 100 caps the main pot at every seat;
    seats 1 and 2 then fight a side pot up to 300 (seat 1's cap); seat 2's
    remaining excess above 300 has nobody left to contest it and is
    returned uncalled — so exactly two pot tiers, not three, is the
    correct outcome for three *distinct* stacks, not a weaker assertion."""
    adapter = HoldemAdapter()
    state = adapter.reset(_cfg(3, starting_stacks=[100, 300, 600]), _deck())
    events = _shove_everyone(adapter, state, 3)

    pots = tuple(state.pots)
    assert len(pots) > 1, "unequal all-in stacks must split into more than one pot tier"
    assert [tuple(sorted(p.player_indices)) for p in pots] == [(0, 1, 2), (1, 2)], (
        "main pot must be eligible to every live seat; the side pot only to the two larger stacks"
    )
    assert pots[0].amount == 300, "main pot: 100 from each of the three seats"
    assert pots[1].amount == 400, "side pot: 200 more each from seats 1 and 2, capped at seat 1's 300 stack"

    pot_awarded = next(e for e in events if e.type == EventType.POT_AWARDED)
    assert len(pot_awarded.payload.pots) == len(pots)
    for pot_view, pot in zip(pot_awarded.payload.pots, pots):
        assert sum(a.amount for a in pot_view.awards) == pot.amount == pot_view.amount
        assert {a.seat for a in pot_view.awards} <= set(pot.player_indices), (
            "a pot can only be awarded to seats eligible for it"
        )


def test_seat_status_matches_derivation_table_for_fold_all_in_and_active() -> None:
    """§10 checklist: "Seat status matches the derivation table across fold /
    all-in / active." §10's trap: `state.statuses`/`state.stacks`, not
    `state.folded_status`/`state.all_in_status` (last-operation flags, not
    per-seat arrays) — this test only exercises the adapter's public `view`,
    so it can't accidentally pass by reading the wrong pokerkit field."""
    adapter = HoldemAdapter()
    state = adapter.reset(_cfg(3), _deck())

    folded_seat = _to_act(adapter, state)
    assert folded_seat is not None
    adapter.apply(state, folded_seat, Action(type=ActionType.FOLD))
    assert adapter.view(state, folded_seat).you.status == SeatStatus.FOLDED

    remaining = [s for s in range(3) if s != folded_seat]
    all_in_seat = _to_act(adapter, state)
    assert all_in_seat in remaining, "the seat to act next must be one of the two live seats"
    active_seat = next(s for s in remaining if s != all_in_seat)
    obs = adapter.view(state, active_seat)
    assert obs.you.status == SeatStatus.ACTIVE

    legal = adapter.legal_actions(state, all_in_seat)
    raise_spec = next(a for a in legal if a.type == ActionType.RAISE)
    assert raise_spec.max_to is not None
    adapter.apply(state, all_in_seat, Action(type=ActionType.RAISE, to=raise_spec.max_to))
    assert adapter.view(state, all_in_seat).you.status == SeatStatus.ALL_IN


def test_committed_hand_is_omitted_once_the_hand_is_complete() -> None:
    """§10 checklist: "committed_hand is not read after chips are pushed."
    §10's derivation table: `starting_stacks[i] - stacks[i]` goes negative
    for winners once chips are pushed, and there's no defined instant before
    automated pushing — so the field must be omitted (None, not a stale or
    negative number) once `phase == hand_complete`."""
    adapter = HoldemAdapter()
    state = adapter.reset(_cfg(2), _deck())
    seat = _to_act(adapter, state)
    assert seat is not None
    adapter.apply(state, seat, Action(type=ActionType.FOLD))  # uncontested — ends the hand immediately

    for s in range(2):
        obs = adapter.view(state, s)
        assert obs.phase == Phase.HAND_COMPLETE
        assert obs.you.committed_hand is None


def test_everyone_folding_to_one_seat_is_uncontested_with_no_showdown_event() -> None:
    """§10 checklist: "Everyone folding to one seat emits pot_awarded with
    reason: 'uncontested' and no showdown event." §3.0: the most common way
    a hand ends, not an edge case — no reveal happens because nothing needs
    resolving."""
    adapter = HoldemAdapter()
    state = adapter.reset(_cfg(3), _deck())

    events: list[object] = []
    for _ in range(3):
        seat = _to_act(adapter, state)
        if seat is None:
            break
        events = adapter.apply(state, seat, Action(type=ActionType.FOLD))
        if adapter.is_terminal(state):
            break

    assert not any(e.type == EventType.SHOWDOWN for e in events), "no discretion existed — no showdown should fire"
    pot_awarded = next(e for e in events if e.type == EventType.POT_AWARDED)
    assert all(p.reason == PotAwardReason.UNCONTESTED for p in pot_awarded.payload.pots)


def test_showdown_with_one_non_all_in_seat_offers_show_and_muck() -> None:
    """§10 checklist: "Showdown with one non-all-in seat exposes show/muck in
    legal_actions." §3.1: showdown is a turn phase, not an instantaneous
    event, whenever at least one contested seat isn't all-in."""
    adapter = HoldemAdapter()
    state = adapter.reset(_cfg(2, starting_stack=5000), _deck())
    _check_down_to_showdown(adapter, state, 2)

    seat = _to_act(adapter, state)
    assert seat is not None, "expected a discretionary show/muck decision, but nobody was left to act"
    assert adapter.view(state, seat).phase == Phase.SHOWDOWN
    legal_types = {a.type for a in adapter.legal_actions(state, seat)}
    assert legal_types == {ActionType.SHOW, ActionType.MUCK}


def test_can_win_now_primitive_is_available_for_the_showdown_timeout_rule() -> None:
    """§10 checklist: "seat_timed_out for a showdown-phase timeout omits
    forced_action." M1's public API has no way to actually trigger a
    timeout — the turn clock is inert until M2 (§8) — so this cannot be
    driven end-to-end yet. Interpretation (docs/DECISIONS.md): test the
    primitive §3.2 says the forced-action rule must be built on,
    `state.can_win_now(seat)`, since that's the actual engine-level
    contract this checklist item depends on, and it's the part game-holdem
    owns today."""
    adapter = HoldemAdapter()
    state = adapter.reset(_cfg(2, starting_stack=5000), _deck())
    _check_down_to_showdown(adapter, state, 2)
    seat = _to_act(adapter, state)
    assert seat is not None
    assert isinstance(state.can_win_now(seat), bool)


def test_pot_awarded_awards_sum_exactly_to_the_pot_amount() -> None:
    """§10 checklist: "pot_awarded.awards[].amount sums exactly to the pot
    amount, including odd-chip splits." awards[] is authoritative — a split
    pot with an odd chip must give winners unequal amounts rather than lose
    a chip to rounding (§5)."""
    adapter = HoldemAdapter()
    state = adapter.reset(_cfg(3, sb=1, bb=3, starting_stack=151), _deck())
    events = _shove_everyone(adapter, state, 3)
    pot_awarded = next(e for e in events if e.type == EventType.POT_AWARDED)
    for pot in pot_awarded.payload.pots:
        assert sum(a.amount for a in pot.awards) == pot.amount


def test_3way_preflop_all_in_fully_deals_the_board_and_exposes_every_live_hand() -> None:
    """§10 checklist: "After a 3-way preflop all-in, the board is fully
    dealt and every live hand exposed." §3.1: when every contested seat is
    all-in, there's no discretion left — the server reveals every live hand
    and deals straight through to the river in one motion."""
    adapter = HoldemAdapter()
    state = adapter.reset(_cfg(3, starting_stack=150), _deck())
    events = _shove_everyone(adapter, state, 3)

    assert adapter.is_terminal(state)
    assert len(adapter.view(state, 0).board) == 5

    showdown = next(e for e in events if e.type == EventType.SHOWDOWN)
    revealed_seats = {r.seat for r in showdown.payload.reveals}
    assert revealed_seats == {0, 1, 2}
