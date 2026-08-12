"""Deck/dealing helpers. This module is the *only* place `deal_hole` or
`deal_board` may be called — confined here per docs/PROTOCOL.md §10 so the
"list instead of concatenated string" trap has exactly one place it can be
made, guarded by one assertion.

`pokerkit.State.deal_hole`/`deal_board` want a single concatenated string
per call (`"AsKd"`, not `["As", "Kd"]`). Passing a list silently stores raw
`str` instead of `Card` objects and crashes later inside hand evaluation,
far from the cause — the `isinstance` assert below turns that into an
immediate failure at the call site instead.
"""

from __future__ import annotations

from pokerkit import BoardDealing, Card, HoleDealing, State


def draw(deck: list[str], count: int) -> str:
    """Pop `count` cards off the front of `deck` and concatenate them into
    the single string `deal_hole`/`deal_board` expect."""
    cards = deck[:count]
    del deck[:count]
    assert len(cards) == count, "not enough cards left in the given deck"
    return "".join(cards)


def deal(operation: HoleDealing | BoardDealing) -> tuple[Card, ...]:
    """Assert the just-performed `HoleDealing`/`BoardDealing` operation
    actually parsed `Card` instances, not raw strings, and return them."""
    assert all(isinstance(c, Card) for c in operation.cards), (
        "dealt cards are not Card instances — a list was likely passed to "
        "deal_hole/deal_board instead of one concatenated string"
    )
    return operation.cards


def deal_hole(pk: State, cards: str) -> tuple[Card, ...]:
    return deal(pk.deal_hole(cards))


def deal_board(pk: State, cards: str) -> tuple[Card, ...]:
    return deal(pk.deal_board(cards))
