"""WebSocket push layer over the M1 REST surface (docs/PROTOCOL.md §1, §8).

Not a parallel implementation: `act` routes through `Room.submit_action_for_seat`,
which shares `Room._commit_action` with REST `POST /actions` (`main.py`) — same
validation, same idempotency, same `seq` assignment (invariant 6, one writer per
room). This module only ever talks to `Room` through its public async API; it
never touches `S` or constructs an `Observation` itself (invariant 2).
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect

from packages.room_server.errors import ApiError
from packages.room_server.serialize import to_wire
from packages.room_server.store import Room, RoomStore
from packages.room_server.wire import parse_action

# §5: "client pings every 20s. Server closes a socket silent for 60s."
_HEARTBEAT_TIMEOUT_S = 60.0


def _error_frame(exc: ApiError) -> dict[str, object]:
    """The §8 `error` frame for an `ApiError` — same `code`/`reason`/context
    shape REST error responses use (`errors.py`), just without an HTTP status."""
    return {"t": "error", "code": exc.code.value, "reason": exc.reason, **exc.context}


async def _handle_act(room: Room[Any], seat_index: int | None, msg: dict[str, object], socket: WebSocket) -> None:
    """Client `act` frame: apply one action for `seat_index` through the
    identical path `POST /actions` uses. A spectator connection (`seat_index
    is None`) can never act — there is no seat_token-equivalent credential
    behind it, only the room's public `invite_token`."""
    if seat_index is None:
        await socket.send_json({"t": "error", "code": "invalid_token", "reason": "spectators cannot act"})
        return
    try:
        request_id = str(msg["request_id"])
        action_in = msg.get("action")
        if not isinstance(action_in, dict):
            raise TypeError("act.action must be an object")
        to = action_in.get("to")
        if to is not None and not isinstance(to, int):
            raise ValueError("act.action.to must be an integer")
        action = parse_action(str(action_in.get("type", "")), to)
    except ApiError as exc:
        await socket.send_json(_error_frame(exc))
        return
    except (KeyError, TypeError, ValueError) as exc:
        await socket.send_json({"t": "error", "code": "bad_request", "reason": str(exc)})
        return

    table_talk = msg.get("table_talk")
    if table_talk is not None and not isinstance(table_talk, str):
        await socket.send_json({"t": "error", "code": "bad_request", "reason": "act.table_talk must be a string"})
        return

    try:
        await room.submit_action_for_seat(seat_index, request_id, action, table_talk)
    except ApiError as exc:
        await socket.send_json(_error_frame(exc))


async def _handle_resume(room: Room[Any], seat_index: int | None, msg: dict[str, object], socket: WebSocket) -> None:
    """Client `resume` frame (§8): replay events since `since`, then — for a
    seat connection only — the `state` snapshot as of that exact sequence."""
    raw_since = msg.get("since", -1)
    if not isinstance(raw_since, (int, str)):
        await socket.send_json({"t": "error", "code": "bad_request", "reason": "resume.since must be an integer"})
        return
    try:
        since = int(raw_since)
    except ValueError:
        await socket.send_json({"t": "error", "code": "bad_request", "reason": "resume.since must be an integer"})
        return

    replay, _latest, obs = await room.resume(seat_index, since)
    for ev in replay:
        await socket.send_json({"t": "event", "payload": to_wire(ev)})
    if obs is not None:
        await socket.send_json({"t": "state", "payload": to_wire(obs)})


async def _pump(socket: WebSocket, queue: asyncio.Queue[dict[str, object]]) -> None:
    """Drain one connection's broadcast queue (`Room._broadcast`) onto its
    socket. Runs concurrently with the read loop below so a room mutation
    never blocks on this connection's send."""
    while True:
        frame = await queue.get()
        await socket.send_json(frame)


def register_ws_route(app: FastAPI, store: RoomStore[Any]) -> None:
    """Register `GET /v1/rooms/{room_id}/ws` (docs/PROTOCOL.md §8) against `store`."""

    @app.websocket("/v1/rooms/{room_id}/ws")
    async def room_ws(websocket: WebSocket, room_id: str, ticket: str = Query(...)) -> None:
        await websocket.accept()

        try:
            room = store.get(room_id)
        except ApiError as exc:
            await websocket.send_json(_error_frame(exc))
            await websocket.close()
            return

        try:
            seat_index = await room.consume_ws_ticket(ticket)
        except ApiError as exc:
            await websocket.send_json(_error_frame(exc))
            await websocket.close()
            return

        # `hello`: full replay from the start of the log. The client is not
        # assumed to have any prior state on a fresh connect; a genuine
        # reconnect follows up with its own `resume {since}` for the atomic
        # incremental catch-up (§8 tells clients to dedupe on `seq`, which is
        # exactly what covers the overlap between this and that).
        replay, latest = await room.events_since(-1)
        await websocket.send_json(
            {"t": "hello", "seq": latest, "seat": seat_index, "replay": [to_wire(ev) for ev in replay]}
        )
        if seat_index is not None:
            obs = await room.view_by_index(seat_index)
            await websocket.send_json({"t": "state", "payload": to_wire(obs)})

        queue = room.subscribe()
        pump_task = asyncio.create_task(_pump(websocket, queue))
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(websocket.receive_json(), timeout=_HEARTBEAT_TIMEOUT_S)
                except asyncio.TimeoutError:
                    break

                frame_type = msg.get("t") if isinstance(msg, dict) else None
                if frame_type == "ping":
                    await websocket.send_json({"t": "pong"})
                elif frame_type == "act":
                    await _handle_act(room, seat_index, msg, websocket)
                elif frame_type == "resume":
                    await _handle_resume(room, seat_index, msg, websocket)
                else:
                    await websocket.send_json(
                        {"t": "error", "code": "bad_request", "reason": f"unknown frame type {frame_type!r}"}
                    )
        except WebSocketDisconnect:
            pass
        finally:
            pump_task.cancel()
            room.unsubscribe(queue)
            try:
                await websocket.close()
            except RuntimeError:
                # Already closed (e.g. the client disconnected first) —
                # closing twice is a client-visible no-op, not a real error.
                pass
