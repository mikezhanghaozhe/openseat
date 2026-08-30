"""Unit tests for the M2 WebSocket layer (docs/PROTOCOL.md §1, §8), driven
over HTTP + WS against StubAdapter. Not under tests/contract/ — this
package's own scratchpad (AGENTS.md)."""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from packages.room_server import store as store_module
from packages.room_server.main import create_app


def _rid() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app(allow_fixed_seed=True)
    with TestClient(app) as c:
        yield c


def _create_room(client: TestClient, turn_seconds: int | None = None) -> dict[str, object]:
    config: dict[str, object] = {"rounds": 3}
    if turn_seconds is not None:
        config["turn_seconds"] = turn_seconds
    resp = client.post("/v1/rooms", json={"game": "stub", "seats": 2, "config": config})
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


def _ticket(client: TestClient, room_id: str, bearer: str) -> str:
    resp = client.post(f"/v1/rooms/{room_id}/ws-ticket", headers={"Authorization": f"Bearer {bearer}"})
    assert resp.status_code == 200, resp.text
    ticket: str = resp.json()["ticket"]
    return ticket


def _setup(client: TestClient, turn_seconds: int | None = None) -> dict[str, object]:
    room = _create_room(client, turn_seconds=turn_seconds)
    room_id = str(room["room_id"])
    s0 = _claim(client, room_id, str(room["invite_token"]), 0, "alice")
    s1 = _claim(client, room_id, str(room["invite_token"]), 1, "bob")
    start = client.post(f"/v1/rooms/{room_id}/start", json={"host_token": room["host_token"]})
    assert start.status_code == 200, start.text
    return {
        "room_id": room_id,
        "invite_token": room["invite_token"],
        "host_token": room["host_token"],
        "tok0": s0["seat_token"],
        "tok1": s1["seat_token"],
    }


# -- push-only play -----------------------------------------------------------


def test_two_clients_play_a_hand_over_ws_with_push_only(client: TestClient) -> None:
    ctx = _setup(client)
    room_id = ctx["room_id"]
    t0 = _ticket(client, room_id, ctx["tok0"])
    t1 = _ticket(client, room_id, ctx["tok1"])

    with (
        client.websocket_connect(f"/v1/rooms/{room_id}/ws?ticket={t0}") as ws0,
        client.websocket_connect(f"/v1/rooms/{room_id}/ws?ticket={t1}") as ws1,
    ):
        hello0 = ws0.receive_json()
        assert hello0["t"] == "hello"
        assert hello0["seat"] == 0
        state0 = ws0.receive_json()
        assert state0["t"] == "state"

        hello1 = ws1.receive_json()
        assert hello1["seat"] == 1
        state1 = ws1.receive_json()
        assert state1["t"] == "state"

        # seat 0 folds over WS, never touching REST
        ws0.send_json({"t": "act", "request_id": _rid(), "action": {"type": "fold"}})

        # both connections see the push (event) frames — pure push, no polling
        seen_types_0 = []
        for _ in range(3):
            frame = ws0.receive_json()
            if frame["t"] == "event":
                seen_types_0.append(frame["payload"]["type"])
        assert "action_taken" in seen_types_0
        assert "hand_complete" in seen_types_0

        seen_types_1 = []
        for _ in range(3):
            frame = ws1.receive_json()
            if frame["t"] == "event":
                seen_types_1.append(frame["payload"]["type"])
        assert "action_taken" in seen_types_1
        assert "hand_complete" in seen_types_1


# -- reconnect / resume --------------------------------------------------------


def test_disconnect_reconnect_resume_sees_every_event_once_via_dedupe(client: TestClient) -> None:
    ctx = _setup(client)
    room_id = ctx["room_id"]
    t0 = _ticket(client, room_id, ctx["tok0"])

    last_seq = -1
    with client.websocket_connect(f"/v1/rooms/{room_id}/ws?ticket={t0}") as ws0:
        hello = ws0.receive_json()
        assert hello["t"] == "hello"
        last_seq = max([e["seq"] for e in hello["replay"]], default=-1)
        state = ws0.receive_json()
        assert state["t"] == "state"
    # connection closed here — seat is NOT vacated (§8)

    # a real client action arrives while nobody is connected
    fold = client.post(
        f"/v1/rooms/{room_id}/actions",
        json={"seat_token": ctx["tok0"], "request_id": _rid(), "action": {"type": "fold"}},
    )
    assert fold.status_code == 200, fold.text

    # reconnect with a *fresh* ticket and resume from the last seq seen
    t0b = _ticket(client, room_id, ctx["tok0"])
    with client.websocket_connect(f"/v1/rooms/{room_id}/ws?ticket={t0b}") as ws0b:
        ws0b.receive_json()  # hello (full replay again — client dedupes on seq)
        ws0b.receive_json()  # state
        ws0b.send_json({"t": "resume", "since": last_seq})

        replayed_seqs: list[int] = []
        got_state = False
        while not got_state:
            frame = ws0b.receive_json()
            if frame["t"] == "event":
                replayed_seqs.append(frame["payload"]["seq"])
            elif frame["t"] == "state":
                got_state = True

    assert replayed_seqs == sorted(replayed_seqs)
    assert all(s > last_seq for s in replayed_seqs)
    assert len(replayed_seqs) == len(set(replayed_seqs))  # no duplicates within this one resume batch
    # every event caused by the fold shows up exactly once in the replay
    events_resp = client.get(f"/v1/rooms/{room_id}/events?since={last_seq}").json()["events"]
    assert replayed_seqs == [e["seq"] for e in events_resp]


# -- turn clock -----------------------------------------------------------------


def test_turn_clock_fires_and_auto_folds_unresponsive_seat(client: TestClient) -> None:
    ctx = _setup(client, turn_seconds=1)
    room_id = ctx["room_id"]

    # nobody ever acts — wait past the deadline for the server's own timer
    deadline = time.time() + 5
    hand_no_status = None
    while time.time() < deadline:
        result = client.get(f"/v1/rooms/{room_id}/events?since=-1").json()
        types = [e["type"] for e in result["events"]]
        if "seat_timed_out" in types:
            hand_no_status = types
            break
        time.sleep(0.1)

    assert hand_no_status is not None, "turn clock never fired a forced action"
    timed_out = next(e for e in result["events"] if e["type"] == "seat_timed_out")
    assert timed_out["payload"]["seat"] == 0
    # StubAdapter always offers both check and fold (§8: "check if legal, otherwise fold")
    assert timed_out["payload"]["forced_action"] == "check"


# -- spectators -----------------------------------------------------------------


def test_spectator_never_receives_a_state_frame(client: TestClient) -> None:
    ctx = _setup(client)
    room_id = ctx["room_id"]
    spectator_ticket = _ticket(client, room_id, ctx["invite_token"])

    with client.websocket_connect(f"/v1/rooms/{room_id}/ws?ticket={spectator_ticket}") as spec:
        hello = spec.receive_json()
        assert hello["t"] == "hello"
        assert hello["seat"] is None

        client.post(
            f"/v1/rooms/{room_id}/actions",
            json={"seat_token": ctx["tok0"], "request_id": _rid(), "action": {"type": "fold"}},
        )

        frames = [spec.receive_json() for _ in range(3)]
        assert all(f["t"] != "state" for f in frames)
        assert any(f["t"] == "event" for f in frames)

        # a spectator cannot act, either
        spec.send_json({"t": "act", "request_id": _rid(), "action": {"type": "fold"}})
        err = spec.receive_json()
        assert err["t"] == "error"


# -- ticket discipline ----------------------------------------------------------


def test_used_ticket_is_rejected(client: TestClient) -> None:
    ctx = _setup(client)
    room_id = ctx["room_id"]
    ticket = _ticket(client, room_id, ctx["tok0"])

    with client.websocket_connect(f"/v1/rooms/{room_id}/ws?ticket={ticket}") as ws0:
        ws0.receive_json()  # hello
        ws0.receive_json()  # state

    with client.websocket_connect(f"/v1/rooms/{room_id}/ws?ticket={ticket}") as ws0_again:
        frame = ws0_again.receive_json()
        assert frame["t"] == "error"
        assert frame["code"] == "invalid_token"


def test_expired_ticket_is_rejected(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _setup(client)
    room_id = ctx["room_id"]
    monkeypatch.setattr(store_module, "_WS_TICKET_TTL_SECONDS", 0)
    ticket = _ticket(client, room_id, ctx["tok0"])
    time.sleep(0.05)

    with client.websocket_connect(f"/v1/rooms/{room_id}/ws?ticket={ticket}") as ws0:
        frame = ws0.receive_json()
        assert frame["t"] == "error"
        assert frame["code"] == "invalid_token"


# -- idempotency across transports ---------------------------------------------


def test_same_request_id_over_rest_then_ws_does_not_double_apply(client: TestClient) -> None:
    ctx = _setup(client)
    room_id = ctx["room_id"]
    rid = _rid()

    rest_resp = client.post(
        f"/v1/rooms/{room_id}/actions",
        json={"seat_token": ctx["tok0"], "request_id": rid, "action": {"type": "fold"}},
    )
    assert rest_resp.status_code == 200
    rest_body = rest_resp.json()

    t0 = _ticket(client, room_id, ctx["tok0"])
    with client.websocket_connect(f"/v1/rooms/{room_id}/ws?ticket={t0}") as ws0:
        ws0.receive_json()  # hello
        ws0.receive_json()  # state
        ws0.send_json({"t": "act", "request_id": rid, "action": {"type": "fold"}})
        # the retry must not double-apply: no new action_taken/hand_complete
        # is broadcast for it, and the room's log is unchanged.
        events_after = client.get(f"/v1/rooms/{room_id}/events?since=-1").json()["events"]

    hand_completes = [e for e in events_after if e["type"] == "hand_complete"]
    assert len(hand_completes) == 1
    assert rest_body["last_seq"] == events_after[-1]["seq"]
