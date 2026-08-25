"""In-memory rooms: seats, tokens, the per-room event log, and the single
ordered queue each room's mutations go through (invariant 6).

Every mutation to a `Room` — claiming a seat, starting the hand, applying an
action — runs inside `Room.lock`. FastAPI handlers `await` freely between
requests, so without an explicit lock two concurrent requests could both
read `to_act` before either commits. `asyncio.Lock` serializes the
awaitable critical sections within a single process; horizontal scaling
would need to shard by `room_id` instead of sharing this store (§0
invariant 6).
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, replace
from typing import Generic

import jsonschema  # type: ignore[import-untyped]

from packages.engine.types import (
    Action,
    ChatMessage,
    ErrorCode,
    Event,
    EventType,
    HandCompletePayload,
    HandStartedPayload,
    IllegalAction,
    Observation,
    Payload,
    Phase,
    PotAward,
    PotAwardedPayload,
    Reveal,
    RoomCreatedPayload,
    SeatJoinedPayload,
    SeatKind,
    SeatView,
    ShowdownPayload,
    TableTalkPayload,
)
from packages.room_server.adapter import GameAdapter, S
from packages.room_server.errors import ApiError
from packages.room_server.serialize import to_wire
from packages.room_server.tokens import (
    new_host_token,
    new_invite_token,
    new_room_id,
    new_seat_token,
    tokens_equal,
)

_RANKS = "23456789TJQKA"
_SUITS = "cdhs"
PROTOCOL_VERSION = "0.1"


def _now_ms() -> int:
    """Current wall-clock time in milliseconds, used to stamp events' `ts`."""
    return int(time.time() * 1000)


def _fresh_deck() -> list[str]:
    """A standard 52-card deck, in a fixed unshuffled rank/suit order (as
    two-character strings like `"As"`)."""
    return [rank + suit for suit in _SUITS for rank in _RANKS]


def _shuffled_deck(rng: random.Random) -> list[str]:
    """A fresh deck shuffled in place by `rng` (invariant 3: the room's seeded RNG)."""
    deck = _fresh_deck()
    rng.shuffle(deck)
    return deck


@dataclass
class SeatSlot:
    index: int
    status: str = "open"  # "open" | "claimed"
    kind: SeatKind | None = None
    name: str | None = None
    seat_token: str | None = None


@dataclass
class IdempotencyRecord:
    action: Action
    response: dict[str, object]


class Room(Generic[S]):
    """One live room: its seats, bearer tokens, event log, and the adapter
    state `S` it drives (opaque here — see the CRITICAL BOUNDARY note in
    `adapter.py`). All mutation goes through `self.lock` (invariant 6)."""

    def __init__(
        self,
        room_id: str,
        game: str,
        adapter: GameAdapter[S],
        config: dict[str, object],
        seats_total: int,
        room_seed: int,
    ) -> None:
        """
        Args:
            room_id: this room's public id.
            game: the game adapter id this room was created for.
            adapter: the `GameAdapter` instance driving hand logic for this room.
            config: the game-specific config, already validated at creation time.
            seats_total: number of seats to create (all "open" until claimed).
            room_seed: the RNG seed this room's deck shuffles derive from (never serialized).
        """
        self.room_id = room_id
        self.game = game
        self.adapter = adapter
        self.config = config
        self.room_seed = room_seed  # server memory only; never serialized
        self.invite_token = new_invite_token()
        self.host_token = new_host_token()
        self.seats: list[SeatSlot] = [SeatSlot(index=i) for i in range(seats_total)]
        self.seq = -1
        self.events: list[Event] = []
        self.state: S | None = None
        self.started = False
        self.closed = False
        self.start_response: dict[str, object] | None = None
        self.idempotency: dict[tuple[int, str], IdempotencyRecord] = {}
        self.lock = asyncio.Lock()

    # -- internal helpers, only ever called while `self.lock` is held ------

    def _emit(self, event_type: EventType, payload: Payload) -> Event:
        """Build a brand-new event, assign it the room's next `seq`, append
        it to the log, and return it. Use for events the store itself
        originates (e.g. `ROOM_CREATED`, `SEAT_JOINED`)."""
        self.seq += 1
        ev = Event(seq=self.seq, type=event_type, ts=_now_ms(), payload=payload)
        self.events.append(ev)
        return ev

    def _stamp(self, unstamped: Event) -> Event:
        """Assign a real `seq`/`ts` to an adapter-produced placeholder event
        (invariant 5: `seq` is monotonic per room, assigned only here), append it, and return it."""
        self.seq += 1
        ev = replace(unstamped, seq=self.seq, ts=_now_ms())
        self.events.append(ev)
        return ev

    def _seat_by_token(self, seat_token: str | None) -> SeatSlot:
        """Resolve a bearer `seat_token` to its claimed `SeatSlot`.

        Raises:
            ApiError: `INVALID_TOKEN` if the token is missing or doesn't match any claimed seat.
        """
        for slot in self.seats:
            if slot.status == "claimed" and tokens_equal(seat_token, slot.seat_token):
                return slot
        raise ApiError(ErrorCode.INVALID_TOKEN, "seat_token missing, unknown, or for another room")

    def _chat(self) -> list[ChatMessage]:
        """Derive the full table-talk history by scanning the event log for `TABLE_TALK` events."""
        chat: list[ChatMessage] = []
        for ev in self.events:
            if isinstance(ev.payload, TableTalkPayload):
                chat.append(ChatMessage(seq=ev.seq, seat=ev.payload.seat, name=ev.payload.name, text=ev.payload.text))
        return chat

    def _overlay_seat_metadata(self, seats: list[SeatView]) -> list[SeatView]:
        """Fill in each `SeatView`'s `kind`/`name` from this room's
        join-time metadata — fields the adapter's `view()` cannot know
        (see `GameAdapter.view` docstring)."""
        by_index = {s.index: s for s in self.seats}
        out: list[SeatView] = []
        for sv in seats:
            slot = by_index.get(sv.seat)
            if slot is not None and slot.kind is not None and slot.name is not None:
                sv = replace(sv, kind=slot.kind, name=slot.name)
            out.append(sv)
        return out

    def _overlay(self, obs: Observation) -> Observation:
        """Fill in the room-server-owned envelope fields an adapter's `Observation`
        cannot know on its own: `protocol_version`, `seq`, `room_id`, seat
        metadata, and `chat`."""
        slot = self.seats[obs.you.seat]
        you = obs.you
        if slot.name is not None:
            you = replace(you, name=slot.name)
        return replace(
            obs,
            protocol_version=PROTOCOL_VERSION,
            seq=self.seq,
            room_id=self.room_id,
            you=you,
            seats=self._overlay_seat_metadata(obs.seats),
            chat=self._chat(),
        )

    def _claimed_seats(self) -> list[SeatJoinedPayload]:
        """Every currently-claimed seat's join-time metadata, for
        `GameAdapter.waiting_view` before the hand has started."""
        return [
            SeatJoinedPayload(seat=s.index, name=s.name, kind=s.kind)
            for s in self.seats
            if s.status == "claimed" and s.name is not None and s.kind is not None
        ]

    # -- public API, each acquires the room's lock --------------------------

    async def claim_seat(
        self, invite_token: str | None, seat_index: int | None, kind: str, display_name: str
    ) -> SeatSlot:
        """Claim an open seat (or a specific `seat_index`) for a new player,
        issuing its bearer `seat_token`.

        Args:
            invite_token: must match the room's `invite_token`.
            seat_index: specific seat to claim; if None, the first open seat is used.
            kind: seat kind string (e.g. "human"/"model"); must parse as a `SeatKind`.
            display_name: name to show for this seat.

        Raises:
            ApiError: `INVALID_TOKEN`, `BAD_REQUEST` (bad kind or index), `SEAT_TAKEN`, or `ROOM_FULL`.
        """
        async with self.lock:
            if not tokens_equal(invite_token, self.invite_token):
                raise ApiError(ErrorCode.INVALID_TOKEN, "invalid invite_token")
            try:
                seat_kind = SeatKind(kind)
            except ValueError as exc:
                raise ApiError(ErrorCode.BAD_REQUEST, f"unknown seat kind {kind!r}") from exc
            if seat_index is not None:
                if not (0 <= seat_index < len(self.seats)):
                    raise ApiError(ErrorCode.BAD_REQUEST, "seat index out of range")
                slot = self.seats[seat_index]
                if slot.status == "claimed":
                    raise ApiError(ErrorCode.SEAT_TAKEN, "seat already claimed")
            else:
                open_slots = [s for s in self.seats if s.status == "open"]
                if not open_slots:
                    raise ApiError(ErrorCode.ROOM_FULL, "no open seats")
                slot = open_slots[0]
            slot.status = "claimed"
            slot.kind = seat_kind
            slot.name = display_name
            slot.seat_token = new_seat_token()
            self._emit(EventType.SEAT_JOINED, SeatJoinedPayload(seat=slot.index, name=slot.name, kind=slot.kind))
            return slot

    async def start(self, host_token: str | None) -> dict[str, object]:
        """Host-only: begin play once every seat is claimed — shuffle the
        deck from `room_seed`, call `adapter.reset`, and emit the hand-start
        events from `adapter.setup_events`. Idempotent: a second call with
        the same host_token after the room has started returns the same
        cached `start_response` rather than starting a second hand.

        Args:
            host_token: must match the room's `host_token`.

        Raises:
            ApiError: `INVALID_TOKEN` or `SEATS_NOT_FILLED`.
        """
        async with self.lock:
            if not tokens_equal(host_token, self.host_token):
                raise ApiError(ErrorCode.INVALID_TOKEN, "invalid host_token")
            if self.started:
                assert self.start_response is not None
                return self.start_response
            if any(s.status == "open" for s in self.seats):
                raise ApiError(ErrorCode.SEATS_NOT_FILLED, "every seat must be claimed before /start")

            rng = random.Random(self.room_seed)
            deck = _shuffled_deck(rng)
            cfg: dict[str, object] = {**self.config, "_seats": len(self.seats)}
            state = self.adapter.reset(cfg, deck)
            self.state = state

            stamped = [self._stamp(ev) for ev in self.adapter.setup_events(state)]
            if any(ev.type == EventType.HAND_COMPLETE for ev in stamped):
                self.closed = True
            self.started = True

            hand_started = next(ev for ev in stamped if ev.type == EventType.HAND_STARTED)
            assert isinstance(hand_started.payload, HandStartedPayload)
            to_act = self.adapter.view(state, 0).to_act

            response: dict[str, object] = {
                "hand_no": hand_started.payload.hand_no,
                "to_act": to_act,
                "first_seq": stamped[0].seq,
                "last_seq": stamped[-1].seq,
            }
            self.start_response = response
            return response

    async def submit_action(
        self,
        seat_token: str | None,
        request_id: str,
        action: Action,
        table_talk: str | None,
    ) -> dict[str, object]:
        """Validate and apply one action for the seat owning `seat_token`.

        Args:
            seat_token: bearer token identifying the acting seat.
            request_id: idempotency key — a retry with the same id and the
                same action returns the original response (`replayed: True`)
                instead of re-applying it; the same id with a *different*
                action is a client bug and raises `REQUEST_ID_CONFLICT`.
            action: the submitted action; validated against `legal_actions` before being applied.
            table_talk: optional chat text to emit alongside the action.

        Raises:
            ApiError: `REQUEST_ID_CONFLICT`, `ROOM_CLOSED`, `NOT_YOUR_TURN`, or `ILLEGAL_ACTION`.
        """
        async with self.lock:
            seat = self._seat_by_token(seat_token)

            # Idempotency is checked before the closed/turn gates: a client
            # retrying the very request that closed the room (its response
            # was dropped in flight) must still get its original result, not
            # 410. Only requests that were never applied are subject to the
            # gates below. Reservation itself happens only on commit, further
            # down — this is strictly a replay lookup.
            key = (seat.index, request_id)
            existing = self.idempotency.get(key)
            if existing is not None:
                if existing.action == action:
                    return {**existing.response, "replayed": True}
                raise ApiError(ErrorCode.REQUEST_ID_CONFLICT, "request_id already used with a different action")

            if self.closed:
                raise ApiError(ErrorCode.ROOM_CLOSED, "room is closed")
            if self.state is None:
                raise ApiError(ErrorCode.NOT_YOUR_TURN, "hand has not started")

            obs = self.adapter.view(self.state, seat.index)
            if obs.to_act != seat.index:
                raise ApiError(
                    ErrorCode.NOT_YOUR_TURN,
                    "not this seat's turn",
                    legal_actions=[to_wire(a) for a in obs.legal_actions],
                )

            try:
                adapter_events = self.adapter.apply(self.state, seat.index, action)
            except IllegalAction as exc:
                raise ApiError(
                    ErrorCode.ILLEGAL_ACTION,
                    exc.reason,
                    legal_actions=[to_wire(a) for a in exc.legal_actions],
                ) from exc

            to_stamp: list[Event] = []
            if table_talk:
                to_stamp.append(
                    Event(
                        seq=0,
                        type=EventType.TABLE_TALK,
                        ts=0,
                        payload=TableTalkPayload(seat=seat.index, name=seat.name or "", text=table_talk),
                    )
                )
            to_stamp.extend(adapter_events)
            stamped = [self._stamp(ev) for ev in to_stamp]

            if any(ev.type == EventType.HAND_COMPLETE for ev in stamped):
                self.closed = True

            response: dict[str, object] = {
                "first_seq": stamped[0].seq,
                "last_seq": stamped[-1].seq,
                "accepted": True,
            }
            self.idempotency[key] = IdempotencyRecord(action=action, response=response)
            return response

    async def view(self, seat_token: str | None) -> Observation:
        """The redacted `Observation` for the seat owning `seat_token` —
        `waiting_view` before the hand has started, `view` afterward.

        Raises:
            ApiError: `INVALID_TOKEN` if `seat_token` doesn't match a claimed seat.
        """
        async with self.lock:
            seat = self._seat_by_token(seat_token)
            if self.state is None:
                obs = self.adapter.waiting_view(self.config, self._claimed_seats(), seat.index)
                return self._overlay(obs)
            obs = self.adapter.view(self.state, seat.index)
            return self._overlay(obs)

    async def events_since(self, since: int) -> tuple[list[Event], int]:
        """Events with `seq` strictly greater than `since`, plus the room's
        current `seq` (`latest_seq`) — the polling/reconnect primitive (invariant 5)."""
        async with self.lock:
            latest = self.seq
            return [ev for ev in self.events if ev.seq > since], latest

    async def result(self) -> dict[str, object]:
        """The outcome of the most recently completed hand: pot awards,
        final stacks, and any showdown reveals.

        Raises:
            ApiError: `HAND_IN_PROGRESS` if no hand has completed yet.
        """
        async with self.lock:
            hand_complete = next((ev for ev in reversed(self.events) if ev.type == EventType.HAND_COMPLETE), None)
            if hand_complete is None:
                raise ApiError(ErrorCode.HAND_IN_PROGRESS, "hand not complete")
            pots = [pot for ev in self.events if ev.type == EventType.POT_AWARDED for pot in _pots_of(ev)]
            reveals: list[Reveal] = [r for ev in self.events if ev.type == EventType.SHOWDOWN for r in _reveals_of(ev)]
            payload = hand_complete.payload
            assert isinstance(payload, HandCompletePayload)
            return {
                "hand_no": payload.hand_no,
                "pots": [to_wire(p) for p in pots],
                "final_stacks": payload.stacks,
                "showdown": [to_wire(r) for r in reveals],
            }

    async def summary(self) -> dict[str, object]:
        """The room's lobby-level summary: game id, phase, seats, hand
        number, and overall room status — no redacted table state included."""
        async with self.lock:
            if self.state is not None:
                obs = self.adapter.view(self.state, 0)
                phase = obs.phase
                hand_no = obs.hand_no
            else:
                phase = Phase.WAITING
                hand_no = 0
            status = "complete" if self.closed else ("in_progress" if self.started else "waiting")
            seats_out: list[dict[str, object]] = []
            for s in self.seats:
                entry: dict[str, object] = {"index": s.index, "status": s.status}
                if s.status == "claimed" and s.kind is not None and s.name is not None:
                    entry["kind"] = s.kind.value
                    entry["name"] = s.name
                seats_out.append(entry)
            return {
                "room_id": self.room_id,
                "game": self.game,
                "phase": phase.value,
                "seats": seats_out,
                "hand_no": hand_no,
                "status": status,
            }


def _pots_of(ev: Event) -> list[PotAward]:
    """The pot awards carried by `ev` if it's a `POT_AWARDED` event, else `[]`."""
    if isinstance(ev.payload, PotAwardedPayload):
        return list(ev.payload.pots)
    return []


def _reveals_of(ev: Event) -> list[Reveal]:
    """The hole-card reveals carried by `ev` if it's a `SHOWDOWN` event, else `[]`."""
    if isinstance(ev.payload, ShowdownPayload):
        return list(ev.payload.reveals)
    return []


class RoomStore(Generic[S]):
    """Registry of all live `Room`s, keyed by room id. Owns room creation
    (including config validation) and lookup; per-room mutation lives on `Room` itself."""

    def __init__(self, adapters: dict[str, GameAdapter[S]], allow_fixed_seed: bool) -> None:
        """
        Args:
            adapters: game-id -> `GameAdapter` registry available for room creation.
            allow_fixed_seed: whether `create_room` may accept a caller-supplied RNG seed.
        """
        self._adapters = adapters
        self._allow_fixed_seed = allow_fixed_seed
        self._rooms: dict[str, Room[S]] = {}
        self._lock = asyncio.Lock()

    async def create_room(
        self, game: str, seats: int, config: dict[str, object], seed: int | None
    ) -> Room[S]:
        """Validate `config` against the `game` adapter and create a new,
        empty (all seats open) `Room`.

        Args:
            game: game adapter id; must be registered in `self._adapters`.
            seats: seat count; must be within the adapter's min/max players.
            config: game-specific config; checked against `config_schema` then `adapter.validate_config`.
            seed: optional fixed RNG seed for the room's deck shuffles; only
                accepted if this store was built with `allow_fixed_seed=True`.

        Raises:
            ApiError: `BAD_REQUEST` (unknown game or disallowed seed) or `INVALID_CONFIG`.
        """
        adapter = self._adapters.get(game)
        if adapter is None:
            raise ApiError(ErrorCode.BAD_REQUEST, f"unknown game {game!r}")
        if not (adapter.min_players <= seats <= adapter.max_players):
            raise ApiError(
                ErrorCode.INVALID_CONFIG,
                f"seats must be between {adapter.min_players} and {adapter.max_players}",
            )
        try:
            jsonschema.validate(instance=config, schema=adapter.config_schema)
        except jsonschema.exceptions.ValidationError as exc:
            raise ApiError(ErrorCode.INVALID_CONFIG, exc.message) from exc
        try:
            adapter.validate_config(config, seats)
        except ValueError as exc:
            # config_schema (plain JSON Schema) can't express cross-field
            # constraints like "sb < bb" — validate_config is the adapter's
            # hook for exactly that, called here so a bad config fails at
            # room creation, not later inside reset() at /start (§6). See
            # docs/DECISIONS.md.
            raise ApiError(ErrorCode.INVALID_CONFIG, str(exc)) from exc
        if seed is not None and not self._allow_fixed_seed:
            raise ApiError(ErrorCode.BAD_REQUEST, "seed is only accepted when ARENA_ALLOW_FIXED_SEED=1")

        room_seed = seed if seed is not None else random.SystemRandom().getrandbits(64)
        async with self._lock:
            room_id = new_room_id()
            while room_id in self._rooms:
                room_id = new_room_id()
            room: Room[S] = Room(
                room_id=room_id,
                game=game,
                adapter=adapter,
                config=config,
                seats_total=seats,
                room_seed=room_seed,
            )
            # Emitted before the room is published into `_rooms` below, so no
            # concurrent reader can observe the room without its first event.
            room._emit(EventType.ROOM_CREATED, RoomCreatedPayload(game=game, config=config, seats_total=seats))
            self._rooms[room_id] = room
        return room

    def get(self, room_id: str) -> Room[S]:
        """Look up a room by id.

        Raises:
            ApiError: `ROOM_NOT_FOUND` if no room with `room_id` exists.
        """
        room = self._rooms.get(room_id)
        if room is None:
            raise ApiError(ErrorCode.ROOM_NOT_FOUND, "no such room")
        return room
