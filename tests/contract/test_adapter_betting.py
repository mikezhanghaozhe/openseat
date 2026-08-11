"""§3/§10 betting-rules contract tests, driven directly against
`GameAdapter`. Every test fails at collection today — see conftest.py and
test_adapter_dealing.py's module docstring for why that's expected.
"""

from __future__ import annotations

from packages.game_holdem.adapter import HoldemAdapter

from packages.engine.types import Action, ActionType

RANKS = "23456789TJQKA"
SUITS = "cdhs"


def _deck() -> list[str]:
    return [r + s for s in SUITS for r in RANKS]


def _cfg(seats: int, **overrides: object) -> dict[str, object]:
    base: dict[str, object] = {"sb": 25, "bb": 50, "ante": 0, "starting_stack": 5000, "_seats": seats}
    base.update(overrides)
    return base


def _to_act(adapter: HoldemAdapter, state: object, any_seat: int = 0) -> int | None:
    return adapter.view(state, any_seat).to_act


def test_raise_to_n_moves_stack_by_n_minus_current_bet_not_by_n() -> None:
    """§10 checklist: "raise to N moves the actor's stack by N - bets[i], not
    by N." §2: amounts are always "to", never "by" — this is the single
    most common poker bug, per the protocol doc itself."""
    adapter = HoldemAdapter()
    state = adapter.reset(_cfg(2), _deck())
    seat = _to_act(adapter, state)
    assert seat is not None

    legal = adapter.legal_actions(state, seat)
    raise_spec = next(a for a in legal if a.type == ActionType.RAISE)
    assert raise_spec.min_to is not None

    stack_before = state.stacks[seat]
    bet_before = state.bets[seat]
    target_to = raise_spec.min_to

    adapter.apply(state, seat, Action(type=ActionType.RAISE, to=target_to))

    stack_after = state.stacks[seat]
    assert stack_before - stack_after == target_to - bet_before, (
        "stack moved by the raise-to amount itself, not by (to - already-committed) — "
        "confirms the raise is being treated as raise-BY instead of raise-TO (§2)"
    )


def test_max_raise_to_equals_stack_plus_current_bet_not_stack_alone() -> None:
    """§10 checklist: "max_raise_to == stacks[i] + bets[i], not stacks[i]."
    max_raise_to is the actor's total street capacity, not the remaining
    stack — all-in-for-less needs no special action type because of this
    (§3)."""
    adapter = HoldemAdapter()
    state = adapter.reset(_cfg(2), _deck())
    seat = _to_act(adapter, state)
    assert seat is not None

    legal = adapter.legal_actions(state, seat)
    raise_spec = next(a for a in legal if a.type == ActionType.RAISE)
    assert raise_spec.max_to == state.stacks[seat] + state.bets[seat]


def test_short_stack_all_in_raise_is_still_offered_when_min_equals_max() -> None:
    """§10 checklist: "A short stack whose only raise is all-in (min_to ==
    max_to) still gets raise offered." §3: equality is legal and must be
    offered — it is the short-stack all-in raise. Uses a starting stack
    barely above the big blind so the first raiser's only legal raise is
    their whole remaining stack."""
    adapter = HoldemAdapter()
    state = adapter.reset(_cfg(2, starting_stack=55), _deck())
    seat = _to_act(adapter, state)
    assert seat is not None

    legal = adapter.legal_actions(state, seat)
    raise_specs = [a for a in legal if a.type == ActionType.RAISE]
    assert raise_specs, "a short stack's only-possible raise (all-in) must still appear in legal_actions"
    spec = raise_specs[0]
    assert spec.min_to == spec.max_to == state.stacks[seat] + state.bets[seat]


def test_no_legal_raise_omits_raise_and_never_raises_type_error() -> None:
    """§10 checklist: "A seat with no legal raise (bounds are None) yields
    legal_actions without raise, and does not raise TypeError." §3: PokerKit
    returns None, not a small number, when raising isn't legal for the
    actor — comparing `None <= None` raises TypeError if the bounds aren't
    checked for None first.

    Scenario: once every *other* live seat is already all-in, raising is
    illegal for whoever is left to act — there's no stack left anywhere to
    respond to a raise. Reached by having two of three seats shove their
    whole stack, then checking the third seat's legal_actions."""
    adapter = HoldemAdapter()
    state = adapter.reset(_cfg(3, starting_stack=500), _deck())

    shoves = 0
    while shoves < 2:
        seat = _to_act(adapter, state)
        assert seat is not None, "ran out of actors before two seats could shove"
        legal = adapter.legal_actions(state, seat)
        raise_spec = next((a for a in legal if a.type == ActionType.RAISE), None)
        if raise_spec is not None and raise_spec.max_to is not None:
            adapter.apply(state, seat, Action(type=ActionType.RAISE, to=raise_spec.max_to))
            shoves += 1
        else:
            adapter.apply(state, seat, Action(type=ActionType.CALL))

    seat = _to_act(adapter, state)
    assert seat is not None, "the seat facing two all-in opponents should still have a decision (call/fold)"
    legal = adapter.legal_actions(state, seat)  # must not raise TypeError
    assert not any(a.type == ActionType.RAISE for a in legal), (
        "raising must not be offered once every other live seat is already all-in — "
        "there is no stack left anywhere to respond to a raise"
    )
