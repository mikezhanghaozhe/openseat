"""Unit tests for packages/arena_client, driven entirely against
httpx.MockTransport — the real room server is never started here (it isn't
this package's job to stand one up; see scripts/play_hand.sh for the
end-to-end check against a live server).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable

import httpx
import pytest

from packages.arena_client import RoomClient
from packages.arena_client.errors import (
    ArenaApiError,
    IllegalActionError,
    RequestIdConflictError,
)
from packages.engine.types import (
    Action,
    ActionType,
    EventType,
    Phase,
    PotAwardReason,
    SeatStatus,
)

Handler = Callable[[httpx.Request], httpx.Response]


def _client(handler: Handler) -> RoomClient:
    return RoomClient(transport=httpx.MockTransport(handler))


def _json(status: int, body: dict[str, object]) -> httpx.Response:
    return httpx.Response(status, json=body)


# -- happy path, one per endpoint ------------------------------------------------


def test_create_room_sends_body_and_parses_response() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v1/rooms"
        captured.update(json.loads(request.content))
        return _json(
            201,
            {
                "protocol_version": "0.1",
                "room_id": "r_abc",
                "invite_token": "inv_x",
                "host_token": "hst_x",
                "seats": [{"index": 0, "status": "open"}, {"index": 1, "status": "open"}],
            },
        )

    with _client(handler) as client:
        room = client.create_room("holdem-nl", 2, {"sb": 25, "bb": 50, "starting_stack": 5000})

    assert captured == {"game": "holdem-nl", "seats": 2, "config": {"sb": 25, "bb": 50, "starting_stack": 5000}}
    assert room.room_id == "r_abc"
    assert room.invite_token == "inv_x"
    assert room.host_token == "hst_x"
    assert [s.index for s in room.seats] == [0, 1]
    assert room.seats[0].status == "open"


def test_create_room_omits_seed_key_when_not_given() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return _json(201, {"room_id": "r_x", "invite_token": "i", "host_token": "h", "seats": []})

    with _client(handler) as client:
        client.create_room("holdem-nl", 2, {})
    assert "seed" not in captured


def test_create_room_includes_seed_when_given() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return _json(201, {"room_id": "r_x", "invite_token": "i", "host_token": "h", "seats": []})

    with _client(handler) as client:
        client.create_room("holdem-nl", 2, {}, seed=42)
    assert captured["seed"] == 42


def test_claim_seat_parses_response_and_omits_seat_key_when_not_given() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/rooms/r_abc/seats"
        captured.update(json.loads(request.content))
        return _json(201, {"seat_token": "sea_x", "seat_index": 1})

    with _client(handler) as client:
        claimed = client.claim_seat("r_abc", "inv_x", "human", "alice")

    assert "seat" not in captured
    assert claimed.seat_token == "sea_x"
    assert claimed.seat_index == 1


def test_get_room_parses_summary() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/v1/rooms/r_abc"
        return _json(
            200,
            {
                "room_id": "r_abc",
                "game": "holdem-nl",
                "phase": "waiting",
                "seats": [{"index": 0, "status": "claimed", "kind": "human", "name": "alice"}],
                "hand_no": 0,
                "status": "waiting",
            },
        )

    with _client(handler) as client:
        summary = client.get_room("r_abc")
    assert summary.phase == Phase.WAITING
    assert summary.seats[0].name == "alice"


def test_start_parses_null_to_act_as_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content) == {"host_token": "hst_x"}
        return _json(200, {"hand_no": 1, "to_act": None, "first_seq": 0, "last_seq": 5})

    with _client(handler) as client:
        result = client.start("r_abc", "hst_x")
    assert result.to_act is None
    assert result.first_seq == 0
    assert result.last_seq == 5


def test_view_sends_seat_token_as_bearer_header_never_as_query_param() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/rooms/r_abc/view"
        assert request.headers["authorization"] == "Bearer sea_x"
        assert "sea_x" not in str(request.url), "seat_token must never appear in the URL/query string"
        return _json(
            200,
            {
                "protocol_version": "0.1",
                "seq": 5,
                "room_id": "r_abc",
                "hand_no": 1,
                "phase": "preflop",
                "to_act": 0,
                "button": 0,
                "you": {
                    "seat": 0,
                    "name": "alice",
                    "hole": ["Ah", "Kd"],
                    "stack": 1000,
                    "committed_street": 0,
                    "committed_hand": 0,
                    "status": "active",
                },
                "board": [],
                "pots": [{"index": 0, "amount": 75, "eligible_seats": [0, 1]}],
                "pot_total": 75,
                "seats": [
                    {"seat": 0, "name": "alice", "kind": "human", "stack": 1000, "committed_street": 0, "status": "active"},
                    {"seat": 1, "name": "bob", "kind": "human", "stack": 950, "committed_street": 50, "status": "active"},
                ],
                "to_call": 50,
                "min_raise_to": 100,
                "max_raise_to": 1000,
                "legal_actions": [
                    {"type": "fold"},
                    {"type": "call", "amount": 50},
                    {"type": "raise", "min_to": 100, "max_to": 1000},
                ],
                "chat": [{"seq": 2, "seat": 1, "name": "bob", "text": "gl"}],
                "text": "You are seat 0...",
            },
        )

    with _client(handler) as client:
        obs = client.view("r_abc", "sea_x")

    assert obs.you.hole == ["Ah", "Kd"]
    assert obs.seats[1].name == "bob"
    assert obs.legal_actions[2].type == ActionType.RAISE
    assert obs.legal_actions[2].min_to == 100
    assert obs.chat[0].text == "gl"
    assert obs.you.status == SeatStatus.ACTIVE


def test_view_never_puts_token_in_query_string_even_if_someone_tried() -> None:
    """Structural guarantee, not just a per-test assertion: view() has no
    parameter that could route a token into the URL."""
    import inspect

    sig = inspect.signature(RoomClient.view)
    assert list(sig.parameters) == ["self", "room_id", "seat_token"]


# -- POST /actions: the behaviors this client exists for --------------------------


def test_act_generates_a_fresh_uuid4_request_id_when_not_given() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return _json(200, {"first_seq": 10, "last_seq": 11, "accepted": True})

    with _client(handler) as client:
        client.act("r_abc", "sea_x", Action(type=ActionType.CHECK))

    request_id = captured["request_id"]
    assert isinstance(request_id, str)
    assert uuid.UUID(request_id).version == 4


def test_act_two_calls_never_reuse_a_request_id() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body["request_id"])
        return _json(200, {"first_seq": 1, "last_seq": 1, "accepted": True})

    with _client(handler) as client:
        client.act("r_abc", "sea_x", Action(type=ActionType.CHECK))
        client.act("r_abc", "sea_x", Action(type=ActionType.CHECK))

    assert len(seen) == 2
    assert seen[0] != seen[1]


def test_act_honors_an_explicit_request_id_for_a_deliberate_retry() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return _json(200, {"first_seq": 1, "last_seq": 1, "accepted": True, "replayed": True})

    with _client(handler) as client:
        result = client.act("r_abc", "sea_x", Action(type=ActionType.FOLD), request_id="fixed-id")

    assert captured["request_id"] == "fixed-id"
    assert result.replayed is True


def test_act_omits_to_for_non_raise_and_includes_it_for_raise() -> None:
    bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return _json(200, {"first_seq": 1, "last_seq": 1, "accepted": True})

    with _client(handler) as client:
        client.act("r_abc", "sea_x", Action(type=ActionType.CALL))
        client.act("r_abc", "sea_x", Action(type=ActionType.RAISE, to=300))

    assert "to" not in bodies[0]["action"]
    assert bodies[1]["action"] == {"type": "raise", "to": 300}


def test_act_includes_table_talk_only_when_given() -> None:
    bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return _json(200, {"first_seq": 1, "last_seq": 1, "accepted": True})

    with _client(handler) as client:
        client.act("r_abc", "sea_x", Action(type=ActionType.CHECK))
        client.act("r_abc", "sea_x", Action(type=ActionType.CHECK), table_talk="nice hand")

    assert "table_talk" not in bodies[0]
    assert bodies[1]["table_talk"] == "nice hand"


def test_act_409_illegal_action_raises_with_legal_actions_and_does_not_retry() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return _json(
            409,
            {
                "protocol_version": "0.1",
                "error": "illegal_action",
                "reason": "raise below min_raise_to",
                "legal_actions": [{"type": "fold"}, {"type": "call", "amount": 50}],
            },
        )

    with _client(handler) as client, pytest.raises(IllegalActionError) as excinfo:
        client.act("r_abc", "sea_x", Action(type=ActionType.RAISE, to=10))

    assert call_count == 1, "the client must not retry an illegal_action on its own"
    err = excinfo.value
    assert err.status_code == 409
    assert err.reason == "raise below min_raise_to"
    assert [a.type for a in err.legal_actions] == [ActionType.FOLD, ActionType.CALL]
    assert err.legal_actions[1].amount == 50


def test_act_409_request_id_conflict_raises_dedicated_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json(409, {"error": "request_id_conflict", "reason": "same request_id, different action"})

    with _client(handler) as client, pytest.raises(RequestIdConflictError):
        client.act("r_abc", "sea_x", Action(type=ActionType.FOLD), request_id="reused")


def test_generic_error_code_raises_base_arena_api_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json(404, {"error": "room_not_found", "reason": "no such room"})

    with _client(handler) as client, pytest.raises(ArenaApiError) as excinfo:
        client.get_room("r_missing")

    assert excinfo.value.status_code == 404
    assert excinfo.value.reason == "no such room"
    assert not isinstance(excinfo.value, (IllegalActionError, RequestIdConflictError))


def test_malformed_error_body_still_raises_a_usable_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal server error")

    with _client(handler) as client, pytest.raises(ArenaApiError) as excinfo:
        client.get_room("r_abc")
    assert excinfo.value.status_code == 500


# -- GET /events: since= and payload parsing across event types -----------------


def test_events_passes_since_and_parses_typed_payloads() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/rooms/r_abc/events"
        assert request.url.params["since"] == "7"
        return _json(
            200,
            {
                "events": [
                    {
                        "seq": 8,
                        "type": "action_taken",
                        "ts": 1000,
                        "payload": {
                            "seat": 0,
                            "action": {"type": "raise", "to": 200},
                            "amount_added": 200,
                            "stack_after": 800,
                            "pot_after": 300,
                            "all_in": False,
                        },
                    },
                    {
                        "seq": 9,
                        "type": "board_dealt",
                        "ts": 1001,
                        "payload": {"street": "flop", "cards": ["2c", "3d", "4h"]},
                    },
                    {
                        "seq": 10,
                        "type": "pot_awarded",
                        "ts": 1002,
                        "payload": {
                            "pots": [
                                {
                                    "index": 0,
                                    "amount": 300,
                                    "awards": [{"seat": 0, "amount": 300}],
                                    "reason": "uncontested",
                                }
                            ]
                        },
                    },
                ],
                "latest_seq": 10,
            },
        )

    with _client(handler) as client:
        page = client.events("r_abc", since=7)

    assert page.latest_seq == 10
    assert [e.type for e in page.events] == [EventType.ACTION_TAKEN, EventType.BOARD_DEALT, EventType.POT_AWARDED]
    action_taken = page.events[0].payload
    assert action_taken.action.type == ActionType.RAISE  # type: ignore[union-attr]
    assert action_taken.action.to == 200  # type: ignore[union-attr]
    board_dealt = page.events[1].payload
    assert board_dealt.cards == ["2c", "3d", "4h"]  # type: ignore[union-attr]
    pot_awarded = page.events[2].payload
    assert pot_awarded.pots[0].reason == PotAwardReason.UNCONTESTED  # type: ignore[union-attr]
    assert pot_awarded.pots[0].awards[0].amount == 300  # type: ignore[union-attr]


def test_result_parses_hand_result() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/rooms/r_abc/result"
        return _json(
            200,
            {
                "hand_no": 1,
                "pots": [{"index": 0, "amount": 300, "awards": [{"seat": 0, "amount": 300}], "reason": "showdown"}],
                "final_stacks": [1300, 700],
                "showdown": [{"seat": 0, "hole": ["Ah", "Kd"], "rank_class": "ONE_PAIR", "description": "One pair"}],
            },
        )

    with _client(handler) as client:
        result = client.result("r_abc")

    assert result.hand_no == 1
    assert result.final_stacks == [1300, 700]
    assert result.showdown[0].rank_class == "ONE_PAIR"
    assert result.pots[0].awards[0].seat == 0
