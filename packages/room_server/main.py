"""FastAPI app for the M1 REST surface (docs/PROTOCOL.md §6).

`create_app` takes an adapter registry so a real game package can be wired
in without this module importing it — see AGENTS.md invariant 7 and the
CRITICAL BOUNDARY note in `adapter.py`. `packages/game-holdem` now exists;
`_default_adapters` registers it automatically when it's importable (see
its docstring), so the module-level `app` used by `make dev` / `uvicorn
packages.room_server.main:app` serves real poker, not just the local
`StubAdapter`.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Header, Query, Request
from fastapi.responses import JSONResponse

from packages.engine.types import Action, ActionType, ErrorCode
from packages.room_server import config as server_config
from packages.room_server.adapter import GameAdapter
from packages.room_server.errors import ApiError
from packages.room_server.schemas import (
    ActionRequest,
    ClaimSeatRequest,
    CreateRoomRequest,
    StartRequest,
)
from packages.room_server.serialize import to_wire, to_wire_dict
from packages.room_server.store import PROTOCOL_VERSION, RoomStore
from packages.room_server.stub import StubAdapter

_BEARER_PREFIX = "Bearer "


def _bearer_token(authorization: str | None) -> str | None:
    if authorization is None or not authorization.startswith(_BEARER_PREFIX):
        return None
    token = authorization[len(_BEARER_PREFIX) :].strip()
    return token or None


def _parse_action(action_in: ActionRequest) -> Action:
    try:
        action_type = ActionType(action_in.action.type)
    except ValueError as exc:
        raise ApiError(ErrorCode.BAD_REQUEST, f"unknown action type {action_in.action.type!r}") from exc
    return Action(type=action_type, to=action_in.action.to)


def _default_adapters() -> dict[str, GameAdapter[Any]]:
    """The registry `create_app()` uses when no explicit `adapters=` is
    given — what `make dev`'s module-level `app` (below) and any test that
    imports `app` directly gets. Always includes the local `StubAdapter`
    (lightweight, dependency-free, used by this package's own unit tests).
    Also registers the real `holdem-nl` game via
    `packages.game_holdem.adapter.HoldemAdapter` when that package is
    importable, so the *default* server is actually playable poker instead
    of a placeholder that only speaks a toy game.

    A plain `try/except ImportError`, not a hard dependency: this package
    still never assumes game-holdem exists as a matter of design (AGENTS.md
    invariant 7 — nothing here imports `pokerkit`, directly or otherwise;
    `packages/game_holdem` is the only package permitted to). It just uses
    the real game when it's present, the same way `scripts/_serve_holdem.py`
    already did explicitly — folded in here so that script and `make dev`
    aren't two different ways to get two different servers. Explicit
    `create_app(adapters=...)` calls are unaffected and remain the primary,
    documented extension point. See docs/DECISIONS.md."""
    adapters: dict[str, GameAdapter[Any]] = {"stub": StubAdapter()}
    try:
        from packages.game_holdem.adapter import HoldemAdapter
    except ImportError:
        pass
    else:
        adapters["holdem-nl"] = HoldemAdapter()
    return adapters


def create_app(
    adapters: dict[str, GameAdapter[Any]] | None = None,
    allow_fixed_seed: bool | None = None,
) -> FastAPI:
    registry: dict[str, GameAdapter[Any]] = adapters if adapters is not None else _default_adapters()
    store: RoomStore[Any] = RoomStore(
        adapters=registry,
        allow_fixed_seed=server_config.allow_fixed_seed() if allow_fixed_seed is None else allow_fixed_seed,
    )

    app = FastAPI()

    @app.exception_handler(ApiError)
    async def _handle_api_error(_request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"protocol_version": PROTOCOL_VERSION, **exc.body()},
        )

    @app.post("/v1/rooms", status_code=201)
    async def create_room(body: CreateRoomRequest) -> dict[str, object]:
        room = await store.create_room(body.game, body.seats, body.config, body.seed)
        return {
            "protocol_version": PROTOCOL_VERSION,
            "room_id": room.room_id,
            "invite_token": room.invite_token,
            "host_token": room.host_token,
            "seats": [{"index": s.index, "status": s.status} for s in room.seats],
        }

    @app.post("/v1/rooms/{room_id}/seats", status_code=201)
    async def claim_seat(room_id: str, body: ClaimSeatRequest) -> dict[str, object]:
        room = store.get(room_id)
        slot = await room.claim_seat(body.invite_token, body.seat, body.kind, body.display_name)
        return {
            "protocol_version": PROTOCOL_VERSION,
            "seat_token": slot.seat_token,
            "seat_index": slot.index,
        }

    @app.get("/v1/rooms/{room_id}")
    async def room_summary(room_id: str) -> dict[str, object]:
        room = store.get(room_id)
        return {"protocol_version": PROTOCOL_VERSION, **(await room.summary())}

    @app.post("/v1/rooms/{room_id}/start")
    async def start_room(room_id: str, body: StartRequest) -> dict[str, object]:
        room = store.get(room_id)
        result = await room.start(body.host_token)
        return {"protocol_version": PROTOCOL_VERSION, **result}

    @app.get("/v1/rooms/{room_id}/view")
    async def view_room(room_id: str, authorization: str | None = Header(default=None)) -> dict[str, object]:
        room = store.get(room_id)
        seat_token = _bearer_token(authorization)
        obs = await room.view(seat_token)
        return to_wire_dict(obs)

    @app.post("/v1/rooms/{room_id}/actions")
    async def submit_action(room_id: str, body: ActionRequest) -> dict[str, object]:
        room = store.get(room_id)
        action = _parse_action(body)
        result = await room.submit_action(body.seat_token, body.request_id, action, body.table_talk)
        return {"protocol_version": PROTOCOL_VERSION, **result}

    @app.get("/v1/rooms/{room_id}/events")
    async def room_events(room_id: str, since: int = Query(default=0)) -> dict[str, object]:
        room = store.get(room_id)
        events, latest_seq = await room.events_since(since)
        return {
            "protocol_version": PROTOCOL_VERSION,
            "events": [to_wire(ev) for ev in events],
            "latest_seq": latest_seq,
        }

    @app.get("/v1/rooms/{room_id}/result")
    async def room_result(room_id: str) -> dict[str, object]:
        room = store.get(room_id)
        result = await room.result()
        return {"protocol_version": PROTOCOL_VERSION, **result}

    return app


app = create_app()
