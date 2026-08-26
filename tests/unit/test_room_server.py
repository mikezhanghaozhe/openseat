"""Unit tests for packages/room_server, driven entirely over HTTP against
StubAdapter (packages/game-holdem does not exist yet). Not under
tests/contract/ — these are room-server's own scratchpad (AGENTS.md)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from packages.room_server.main import create_app


def _rid() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app(allow_fixed_seed=True)
    with TestClient(app) as c:
        yield c


def _create_room(client: TestClient, seats: int = 2, rounds: int = 1, seed: int | None = None) -> dict[str, object]:
    body: dict[str, object] = {"game": "stub", "seats": seats, "config": {"rounds": rounds}}
    if seed is not None:
        body["seed"] = seed
    resp = client.post("/v1/rooms", json=body)
    assert resp.status_code == 201, resp.text
    result: dict[str, object] = resp.json()
    return result


def _claim(client: TestClient, room_id: str, invite: str, seat: int, name: str) -> dict[str, object]:
    resp = client.post(
        f"/v1/rooms/{room_id}/seats",
        json={"invite_token": invite, "seat": seat, "kind": "human", "display_name": name},
    )
    assert resp.status_code == 201, resp.text
    result: dict[str, object] = resp.json()
    return result


def _start(client: TestClient, room_id: str, host_token: str) -> dict[str, object]:
    resp = client.post(f"/v1/rooms/{room_id}/start", json={"host_token": host_token})
    assert resp.status_code == 200, resp.text
    result: dict[str, object] = resp.json()
    return result


def _setup_two_seat_room(client: TestClient, rounds: int = 1, seed: int | None = None) -> dict[str, str]:
    room = _create_room(client, seats=2, rounds=rounds, seed=seed)
    room_id = str(room["room_id"])
    s0 = _claim(client, room_id, str(room["invite_token"]), 0, "alice")
    s1 = _claim(client, room_id, str(room["invite_token"]), 1, "bob")
    _start(client, room_id, str(room["host_token"]))
    return {
        "room_id": room_id,
        "invite_token": str(room["invite_token"]),
        "host_token": str(room["host_token"]),
        "tok0": str(s0["seat_token"]),
        "tok1": str(s1["seat_token"]),
    }


# -- happy path -------------------------------------------------------------


def test_full_hand_to_completion(client: TestClient) -> None:
    ctx = _setup_two_seat_room(client)
    resp = client.post(
        f"/v1/rooms/{ctx['room_id']}/actions",
        json={"seat_token": ctx["tok0"], "request_id": _rid(), "action": {"type": "fold"}},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["accepted"] is True
    assert body["first_seq"] < body["last_seq"]

    result = client.get(f"/v1/rooms/{ctx['room_id']}/result")
    assert result.status_code == 200
    payload = result.json()
    assert payload["hand_no"] == 1
    assert payload["final_stacks"] == [1000, 1000]
    assert payload["pots"][0]["awards"] == [{"seat": 1, "amount": 0}]


def test_canonical_setup_event_order(client: TestClient) -> None:
    ctx = _setup_two_seat_room(client)
    events = client.get(f"/v1/rooms/{ctx['room_id']}/events?since=-1").json()["events"]
    types = [e["type"] for e in events]
    assert types == [
        "room_created",
        "seat_joined",
        "seat_joined",
        "hand_started",
        "hole_cards_dealt",
        "action_required",
    ]
    assert [e["seq"] for e in events] == list(range(len(events)))


# -- token discipline (§6 GET /view) ----------------------------------------


def test_view_requires_seat_token_specifically(client: TestClient) -> None:
    ctx = _setup_two_seat_room(client)
    room_id = ctx["room_id"]

    # missing token
    assert client.get(f"/v1/rooms/{room_id}/view").status_code == 401

    # invite_token is not a seat_token
    r = client.get(f"/v1/rooms/{room_id}/view", headers={"Authorization": f"Bearer {ctx['invite_token']}"})
    assert r.status_code == 401

    # host_token is not a seat_token
    r = client.get(f"/v1/rooms/{room_id}/view", headers={"Authorization": f"Bearer {ctx['host_token']}"})
    assert r.status_code == 401

    # seat_token from a different room
    other = _setup_two_seat_room(client)
    r = client.get(f"/v1/rooms/{room_id}/view", headers={"Authorization": f"Bearer {other['tok0']}"})
    assert r.status_code == 401

    # the real thing works
    r = client.get(f"/v1/rooms/{room_id}/view", headers={"Authorization": f"Bearer {ctx['tok0']}"})
    assert r.status_code == 200


def test_view_never_leaks_other_seats_hole_field(client: TestClient) -> None:
    ctx = _setup_two_seat_room(client)
    obs = client.get(
        f"/v1/rooms/{ctx['room_id']}/view", headers={"Authorization": f"Bearer {ctx['tok0']}"}
    ).json()
    for seat in obs["seats"]:
        if seat["seat"] != obs["you"]["seat"]:
            assert "hole" not in seat


# -- idempotency (§6 POST /actions) -----------------------------------------


def test_idempotent_replay_returns_original_result(client: TestClient) -> None:
    ctx = _setup_two_seat_room(client)
    rid = _rid()
    body = {"seat_token": ctx["tok0"], "request_id": rid, "action": {"type": "fold"}}
    first = client.post(f"/v1/rooms/{ctx['room_id']}/actions", json=body).json()
    second = client.post(f"/v1/rooms/{ctx['room_id']}/actions", json=body).json()
    assert second["replayed"] is True
    assert second["first_seq"] == first["first_seq"]
    assert second["last_seq"] == first["last_seq"]


def test_idempotent_replay_survives_room_close(client: TestClient) -> None:
    """The action that closes the room must still be replayable afterward —
    otherwise a dropped response on the closing action is unrecoverable."""
    ctx = _setup_two_seat_room(client)
    rid = _rid()
    body = {"seat_token": ctx["tok0"], "request_id": rid, "action": {"type": "fold"}}
    client.post(f"/v1/rooms/{ctx['room_id']}/actions", json=body)  # closes the room
    replay = client.post(f"/v1/rooms/{ctx['room_id']}/actions", json=body)
    assert replay.status_code == 200
    assert replay.json()["replayed"] is True


def test_request_id_conflict_on_different_action_body(client: TestClient) -> None:
    ctx = _setup_two_seat_room(client)
    rid = _rid()
    r1 = client.post(
        f"/v1/rooms/{ctx['room_id']}/actions",
        json={"seat_token": ctx["tok0"], "request_id": rid, "action": {"type": "fold"}},
    )
    assert r1.status_code == 200
    r2 = client.post(
        f"/v1/rooms/{ctx['room_id']}/actions",
        json={"seat_token": ctx["tok0"], "request_id": rid, "action": {"type": "check"}},
    )
    assert r2.status_code == 409
    assert r2.json()["error"] == "request_id_conflict"


def test_illegal_action_does_not_reserve_request_id(client: TestClient) -> None:
    ctx = _setup_two_seat_room(client)
    rid = _rid()
    bad = client.post(
        f"/v1/rooms/{ctx['room_id']}/actions",
        json={"seat_token": ctx["tok0"], "request_id": rid, "action": {"type": "raise", "to": 300}},
    )
    assert bad.status_code == 409
    assert bad.json()["error"] == "illegal_action"

    good = client.post(
        f"/v1/rooms/{ctx['room_id']}/actions",
        json={"seat_token": ctx["tok0"], "request_id": rid, "action": {"type": "fold"}},
    )
    assert good.status_code == 200
    assert "replayed" not in good.json()


def test_table_talk_excluded_from_idempotency_identity(client: TestClient) -> None:
    ctx = _setup_two_seat_room(client)
    rid = _rid()
    r1 = client.post(
        f"/v1/rooms/{ctx['room_id']}/actions",
        json={"seat_token": ctx["tok0"], "request_id": rid, "action": {"type": "fold"}, "table_talk": "gg"},
    )
    assert r1.status_code == 200
    r2 = client.post(
        f"/v1/rooms/{ctx['room_id']}/actions",
        json={"seat_token": ctx["tok0"], "request_id": rid, "action": {"type": "fold"}, "table_talk": "different"},
    )
    assert r2.status_code == 200
    assert r2.json()["replayed"] is True


# -- turn order / errors -----------------------------------------------------


def test_not_your_turn(client: TestClient) -> None:
    ctx = _setup_two_seat_room(client)
    resp = client.post(
        f"/v1/rooms/{ctx['room_id']}/actions",
        json={"seat_token": ctx["tok1"], "request_id": _rid(), "action": {"type": "check"}},
    )
    assert resp.status_code == 403
    assert resp.json()["error"] == "not_your_turn"


def test_illegal_action_leaves_turn_pointer_unchanged(client: TestClient) -> None:
    ctx = _setup_two_seat_room(client)
    bad = client.post(
        f"/v1/rooms/{ctx['room_id']}/actions",
        json={"seat_token": ctx["tok0"], "request_id": _rid(), "action": {"type": "raise", "to": 50}},
    )
    assert bad.status_code == 409
    assert bad.json()["error"] == "illegal_action"
    assert bad.json()["legal_actions"]

    # seat 1 still cannot act — the turn never moved off seat 0
    still_not_turn = client.post(
        f"/v1/rooms/{ctx['room_id']}/actions",
        json={"seat_token": ctx["tok1"], "request_id": _rid(), "action": {"type": "check"}},
    )
    assert still_not_turn.status_code == 403


# -- room lifecycle -----------------------------------------------------------


def test_start_requires_every_seat_filled(client: TestClient) -> None:
    room = _create_room(client, seats=2)
    room_id = str(room["room_id"])
    _claim(client, room_id, str(room["invite_token"]), 0, "alice")
    resp = client.post(f"/v1/rooms/{room_id}/start", json={"host_token": room["host_token"]})
    assert resp.status_code == 409
    assert resp.json()["error"] == "seats_not_filled"


def test_start_is_idempotent(client: TestClient) -> None:
    ctx = _setup_two_seat_room(client)
    again = client.post(f"/v1/rooms/{ctx['room_id']}/start", json={"host_token": ctx["host_token"]})
    assert again.status_code == 200
    events = client.get(f"/v1/rooms/{ctx['room_id']}/events?since=-1").json()["events"]
    assert [e["type"] for e in events].count("hand_started") == 1


def test_reads_never_410_after_close_but_mutations_do(client: TestClient) -> None:
    ctx = _setup_two_seat_room(client)
    client.post(
        f"/v1/rooms/{ctx['room_id']}/actions",
        json={"seat_token": ctx["tok0"], "request_id": _rid(), "action": {"type": "fold"}},
    )
    assert client.get(f"/v1/rooms/{ctx['room_id']}").status_code == 200
    assert client.get(f"/v1/rooms/{ctx['room_id']}/view", headers={"Authorization": f"Bearer {ctx['tok1']}"}).status_code == 200
    assert client.get(f"/v1/rooms/{ctx['room_id']}/events").status_code == 200
    assert client.get(f"/v1/rooms/{ctx['room_id']}/result").status_code == 200

    mutate = client.post(
        f"/v1/rooms/{ctx['room_id']}/actions",
        json={"seat_token": ctx["tok1"], "request_id": _rid(), "action": {"type": "check"}},
    )
    assert mutate.status_code == 410
    assert mutate.json()["error"] == "room_closed"


def test_result_409_before_hand_complete(client: TestClient) -> None:
    ctx = _setup_two_seat_room(client)
    resp = client.get(f"/v1/rooms/{ctx['room_id']}/result")
    assert resp.status_code == 409
    assert resp.json()["error"] == "hand_in_progress"


# -- config validation --------------------------------------------------------


def test_invalid_config_rejected_before_room_created(client: TestClient) -> None:
    resp = client.post("/v1/rooms", json={"game": "stub", "seats": 2, "config": {"rounds": 0}})
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_config"


def test_unknown_game_rejected(client: TestClient) -> None:
    resp = client.post("/v1/rooms", json={"game": "chess", "seats": 2, "config": {}})
    assert resp.status_code == 400


def test_seats_out_of_range_rejected(client: TestClient) -> None:
    resp = client.post("/v1/rooms", json={"game": "stub", "seats": 1, "config": {"rounds": 1}})
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_config"


def test_seed_rejected_without_fixed_seed_flag() -> None:
    app = create_app(allow_fixed_seed=False)
    with TestClient(app) as client:
        resp = client.post("/v1/rooms", json={"game": "stub", "seats": 2, "config": {"rounds": 1}, "seed": 7})
        assert resp.status_code == 400


# -- seat claiming --------------------------------------------------------------


def test_seat_taken_conflict(client: TestClient) -> None:
    room = _create_room(client)
    room_id = str(room["room_id"])
    _claim(client, room_id, str(room["invite_token"]), 0, "alice")
    resp = client.post(
        f"/v1/rooms/{room_id}/seats",
        json={"invite_token": room["invite_token"], "seat": 0, "kind": "human", "display_name": "mallory"},
    )
    assert resp.status_code == 409
    assert resp.json()["error"] == "seat_taken"


def test_claim_seat_bad_invite_token(client: TestClient) -> None:
    room = _create_room(client)
    resp = client.post(
        f"/v1/rooms/{room['room_id']}/seats",
        json={"invite_token": "inv_wrong", "seat": 0, "kind": "human", "display_name": "mallory"},
    )
    assert resp.status_code == 401


# -- determinism (invariant 3) --------------------------------------------------


def test_same_seed_same_actions_same_log_excluding_ts() -> None:
    """M2: `action_required.deadline_ms` is now a room-server-stamped
    absolute wall-clock deadline (docs/PROTOCOL.md §8), not the adapters'
    inert `0` placeholder from M1 — so, like `ts`, it necessarily differs
    between two real-time runs of the same seed and must be stripped
    alongside it for this comparison. See docs/DECISIONS.md."""

    def run() -> list[dict[str, object]]:
        app = create_app(allow_fixed_seed=True)
        with TestClient(app) as c:
            ctx = _setup_two_seat_room(c, seed=12345)
            c.post(
                f"/v1/rooms/{ctx['room_id']}/actions",
                json={"seat_token": ctx["tok0"], "request_id": _rid(), "action": {"type": "fold"}},
            )
            events = c.get(f"/v1/rooms/{ctx['room_id']}/events?since=-1").json()["events"]
            for e in events:
                del e["ts"]
                if e["type"] == "action_required":
                    del e["payload"]["deadline_ms"]
            return list(events)

    assert run() == run()


def test_room_not_found(client: TestClient) -> None:
    assert client.get("/v1/rooms/r_doesnotexist").status_code == 404
