"""Shared fixtures for tests/contract/.

These tests ARE the specification (AGENTS.md). They drive the real HTTP
surface with the real game id, `"holdem-nl"`, never the room server's local
`StubAdapter` — using the stub would make these tests verify the stub
instead of the spec. `packages/game-holdem` does not exist yet, so every
API test here fails today at room creation (`400 unknown game`), and every
adapter test fails at import. That is the expected, correct state: these
tests turn green only when the real game is wired in, unmodified.

`ARENA_ALLOW_FIXED_SEED` must be set before `packages.room_server.main` is
first imported anywhere in the test session, because the room server reads
it once at app-construction time (module import time). Setting it here, at
the top of the first-loaded conftest, is the only reliable place.
"""

from __future__ import annotations

import os

os.environ["ARENA_ALLOW_FIXED_SEED"] = "1"

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from packages.room_server.main import app as ROOM_SERVER_APP

GAME_ID = "holdem-nl"

DEFAULT_CONFIG: dict[str, object] = {
    "sb": 25,
    "bb": 50,
    "ante": 0,
    "starting_stack": 5000,
    "turn_seconds": 30,
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=ROOM_SERVER_APP)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def rid() -> str:
    return str(uuid.uuid4())


# -- thin wrappers over the §6 endpoints -------------------------------------


async def create_room(
    client: httpx.AsyncClient,
    *,
    seats: int = 2,
    config: dict[str, object] | None = None,
    seed: int | None = None,
    game: str = GAME_ID,
) -> httpx.Response:
    body: dict[str, object] = {"game": game, "seats": seats, "config": config or DEFAULT_CONFIG}
    if seed is not None:
        body["seed"] = seed
    return await client.post("/v1/rooms", json=body)


async def claim_seat(
    client: httpx.AsyncClient,
    room_id: str,
    invite_token: str,
    seat: int | None,
    name: str,
    kind: str = "human",
) -> httpx.Response:
    return await client.post(
        f"/v1/rooms/{room_id}/seats",
        json={"invite_token": invite_token, "seat": seat, "kind": kind, "display_name": name},
    )


async def start_room(client: httpx.AsyncClient, room_id: str, host_token: str) -> httpx.Response:
    return await client.post(f"/v1/rooms/{room_id}/start", json={"host_token": host_token})


async def submit_action(
    client: httpx.AsyncClient,
    room_id: str,
    seat_token: str,
    action: dict[str, object],
    *,
    request_id: str | None = None,
    table_talk: str | None = None,
) -> httpx.Response:
    body: dict[str, object] = {
        "seat_token": seat_token,
        "request_id": request_id or rid(),
        "action": action,
    }
    if table_talk is not None:
        body["table_talk"] = table_talk
    return await client.post(f"/v1/rooms/{room_id}/actions", json=body)


async def view(client: httpx.AsyncClient, room_id: str, seat_token: str | None) -> httpx.Response:
    headers = {"Authorization": f"Bearer {seat_token}"} if seat_token else {}
    return await client.get(f"/v1/rooms/{room_id}/view", headers=headers)


async def events(client: httpx.AsyncClient, room_id: str, since: int = -1) -> httpx.Response:
    return await client.get(f"/v1/rooms/{room_id}/events", params={"since": since})


async def result(client: httpx.AsyncClient, room_id: str) -> httpx.Response:
    return await client.get(f"/v1/rooms/{room_id}/result")


async def room_summary(client: httpx.AsyncClient, room_id: str) -> httpx.Response:
    return await client.get(f"/v1/rooms/{room_id}")


async def setup_room(
    client: httpx.AsyncClient,
    n_seats: int = 2,
    config: dict[str, object] | None = None,
    seed: int | None = None,
) -> dict[str, Any]:
    """Create a room, claim every seat, and start the hand. Fails loudly (via
    `assert ..., resp.text`) the moment any setup step isn't 2xx — which, until
    `holdem-nl` exists, is immediately at `create_room` (400 unknown game)."""
    r = await create_room(client, seats=n_seats, config=config, seed=seed)
    assert r.status_code == 201, r.text
    body = r.json()
    room_id = body["room_id"]
    seat_tokens: list[str] = []
    for i in range(n_seats):
        sr = await claim_seat(client, room_id, body["invite_token"], i, f"seat{i}")
        assert sr.status_code == 201, sr.text
        seat_tokens.append(sr.json()["seat_token"])
    start = await start_room(client, room_id, body["host_token"])
    assert start.status_code == 200, start.text
    return {
        "room_id": room_id,
        "invite_token": body["invite_token"],
        "host_token": body["host_token"],
        "seat_tokens": seat_tokens,
        "start": start.json(),
    }
