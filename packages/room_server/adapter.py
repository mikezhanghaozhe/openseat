"""`GameAdapter` protocol shape (docs/PROTOCOL.md §9) plus a trivial stub.

CRITICAL BOUNDARY (invariant 2): `S` — the game state type — is a bound
`TypeVar`. Nothing in this package ever imports a concrete `GameState`. Every
adapter method that touches `S` lives here, behind the protocol, and the room
server calls these methods without ever naming or introspecting the state
type. That is the enforcement mechanism: the type is simply never in scope
anywhere else in this package.

`setup_events` is not part of §9 as written — see docs/DECISIONS.md
"room-server: hand-start events need an adapter hook" for why it was added
and what to flag to the PROTOCOL.md owner.
"""

from __future__ import annotations

from typing import Protocol, TypeVar

from packages.engine.types import (
    Action,
    ActionSpec,
    Event,
    Observation,
)

S = TypeVar("S")


class GameAdapter(Protocol[S]):
    id: str
    min_players: int
    max_players: int
    config_schema: dict[str, object]

    def reset(self, cfg: dict[str, object], deck: list[str]) -> S:
        """The room server draws the shuffle; the adapter does no shuffling
        and knows nothing about seeding (§9)."""
        ...

    def setup_events(self, s: S) -> list[Event]:
        """Not in §9. Returns the hand-start event sequence (`hand_started`,
        any game-specific postings, `hole_cards_dealt`, `action_required`)
        immediately after `reset`. `seq`/`ts` on returned events are
        placeholders — the room server overwrites both before broadcast."""
        ...

    def legal_actions(self, s: S, seat: int) -> list[ActionSpec]: ...

    def apply(self, s: S, seat: int, a: Action) -> list[Event]:
        """Mutates `s` in place and returns the events caused, `seq`/`ts`
        unset (room server assigns both). Must raise `IllegalAction` rather
        than silently correcting anything (§9)."""
        ...

    def view(self, s: S, seat: int) -> Observation:
        """The only place redaction happens (invariant 2). `protocol_version`,
        `seq`, `room_id`, and `chat` are room-server-owned envelope fields the
        adapter cannot know; the room server overlays them afterward. Seat
        `name`/`kind` are also room-server-owned (join-time metadata) and are
        overlaid the same way — see docs/DECISIONS.md."""
        ...

    def is_terminal(self, s: S) -> bool: ...

    def results(self, s: S) -> dict[int, float]: ...
