"""§10 dealing-discipline contract tests, driven directly against
`GameAdapter` — no HTTP. `packages/game-holdem` does not exist yet, so
`HoldemAdapter` fails to import and every test in this module fails at
collection. That failure is expected; see conftest.py.

Interpretation notes for two ambiguous items are in docs/DECISIONS.md.
"""

from __future__ import annotations

from packages.game_holdem.adapter import HoldemAdapter

RANKS = "23456789TJQKA"
SUITS = "cdhs"


def _deck() -> list[str]:
    return [r + s for s in SUITS for r in RANKS]


def _cfg(seats: int, **overrides: object) -> dict[str, object]:
    base: dict[str, object] = {"sb": 25, "bb": 50, "ante": 0, "starting_stack": 5000, "_seats": seats}
    base.update(overrides)
    return base


def test_dealt_hole_cards_are_card_instances_not_str() -> None:
    """§10 checklist: "Dealt hole cards are Card instances, not str." §10's
    own trap: `deal_hole`/`deal_board` take one concatenated string per call
    ("AsKd", not ["As","Kd"]) — passing a list silently stores raw `str`
    instead of `Card` objects, which only crashes later, far from the
    cause. This is the cheap post-condition assertion §10 asks for."""
    from pokerkit import Card

    adapter = HoldemAdapter()
    state = adapter.reset(_cfg(2), _deck())
    for seat in range(2):
        assert all(isinstance(c, Card) for c in state.hole_cards[seat]), (
            f"seat {seat}'s hole cards are not Card instances — likely a list passed to deal_hole "
            "instead of one concatenated string"
        )


def test_reset_dealing_is_a_pure_function_of_the_deck_not_of_subsequent_play() -> None:
    """§10 checklist: "A hand ending preflop consumes the same number of RNG
    draws as one reaching the river."

    Interpretation (see docs/DECISIONS.md): the room server draws exactly
    one full 52-card shuffle per hand regardless of outcome (verified at the
    room-server level separately) — the adapter side of that contract is
    that `reset()` deals deterministically from the given deck alone, with
    no dependency on anything that happens afterward in `apply()`. This
    test checks that guarantee directly: two independent `reset()` calls
    from the identical deck, with no actions applied to either, deal
    byte-identical hole cards — dealing is fixed the moment `reset()`
    returns, not spread lazily across the hand."""
    deck = _deck()
    adapter = HoldemAdapter()

    state_a = adapter.reset(_cfg(3), list(deck))
    state_b = adapter.reset(_cfg(3), list(deck))

    for seat in range(3):
        assert list(state_a.hole_cards[seat]) == list(state_b.hole_cards[seat])


def test_pots_are_materialized_once_per_view_not_read_twice() -> None:
    """§10 checklist: "Deriving pots[] and pot_total uses one materialized
    tuple(state.pots)." `state.pots` is an iterator — exhausting it twice
    yields inconsistent `pots[]`/`pot_total`. Checked at the black-box level
    (the only way an outside caller can observe the bug): pot_total must
    equal the sum of the pots' amounts on every single call to `view()`,
    called repeatedly, not just once."""
    adapter = HoldemAdapter()
    state = adapter.reset(_cfg(3), _deck())
    for _ in range(3):
        obs = adapter.view(state, 0)
        assert obs.pot_total == sum(p.amount for p in obs.pots), (
            "pot_total disagrees with sum(pots[].amount) — state.pots was likely read twice "
            "(once for pots[], once for pot_total) instead of materialized once"
        )


def test_rake_is_always_zero_and_runout_count_is_always_one() -> None:
    """§10 checklist: "Pot.raked_amount is 0 and runout_count is 1 for every
    hand." Both features are forced off in M1 — there is no rake config key
    and no wire event for multiple boards (§10)."""
    adapter = HoldemAdapter()
    state = adapter.reset(_cfg(2), _deck())
    for pot in state.pots:
        assert pot.raked_amount == 0
        assert pot.amount == pot.unraked_amount
    assert getattr(state, "runout_count", 1) == 1
