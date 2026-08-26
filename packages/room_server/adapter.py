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

`validate_config` is likewise not part of §9 — see docs/DECISIONS.md
"room-server + game-holdem: a bad config could crash /start instead of
failing at POST /rooms" for why it was added.
"""

from __future__ import annotations

from typing import Protocol, TypeVar

from packages.engine.types import (
    Action,
    ActionSpec,
    Event,
    Observation,
    SeatJoinedPayload,
)

S = TypeVar("S")


class GameAdapter(Protocol[S]):
    id: str
    min_players: int
    max_players: int
    config_schema: dict[str, object]

    def validate_config(self, cfg: dict[str, object], seats: int) -> None:
        """Not in §9. Raise `ValueError` with a human-readable reason if
        `cfg` is semantically invalid in a way `config_schema` (plain JSON
        Schema) cannot express — e.g. a cross-field constraint like `sb <
        bb`, or `starting_stacks` length against the seat count. Called by
        the room server at `POST /rooms`, after `config_schema` validation
        and before the room is created (§6: "A bad config must fail here,
        not crash inside reset() far from the cause"). `seats` is passed
        separately because it isn't part of `cfg` at this point — `_seats`
        is only injected into the config dict later, at `/start` (see
        `reset()` and docs/DECISIONS.md). Must not raise for any `cfg` that
        already satisfies `config_schema` and has no cross-field problems —
        this is additive, not a second pass at what the schema already
        checks."""
        ...

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

    def legal_actions(self, s: S, seat: int) -> list[ActionSpec]:
        """The actions `seat` may legally take right now, given `s`. Every
        submitted action is checked against this before it touches state (invariant 4)."""
        ...

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

    def waiting_view(self, cfg: dict[str, object], seats: list[SeatJoinedPayload], seat: int) -> Observation:
        """Not in §9 as originally written — see docs/DECISIONS.md "room-
        server: zero Observation construction sites". Builds the `Observation`
        shown before `/start`, when `self.state` is `None` and no cards exist
        yet. `seats` is every currently-claimed seat's join-time metadata;
        `seat` is the index of the seat requesting the view (always present in
        `seats`, since only claimed seats can hold a valid seat_token). Same
        envelope-overlay contract as `view()` — `protocol_version`, `seq`,
        `room_id`, and `chat` are placeholders the room server overlays
        afterward."""
        ...

    def is_terminal(self, s: S) -> bool:
        """Whether the hand represented by `s` has finished resolving."""
        ...

    def results(self, s: S) -> dict[int, float]:
        """Each seat's net outcome for the completed hand, keyed by seat index."""
        ...
