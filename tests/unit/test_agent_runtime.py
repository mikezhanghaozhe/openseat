"""Unit tests for M3 model seats (docs/MILESTONES.md M3): `packages/
agent_runtime` plus its wiring into `packages/room_server`. Driven entirely
over HTTP against the room server's `StubAdapter` (only check/fold legal
actions — plenty to exercise the decision/validation/submission loop
without needing real poker rules) with a mocked `Provider` injected via
`create_app(model_provider_factory=...)`. Not under tests/contract/ — this
package's own scratchpad (AGENTS.md).
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from packages.agent_runtime.policy import default_action
from packages.agent_runtime.types import Provider, ProviderError, RawDecision
from packages.engine.types import ActionSpec, ActionType, Observation
from packages.room_server.main import create_app

# -- default_action: the showdown decision space is [show, muck], not [check, fold] --


def test_default_action_prefers_show_over_fold_at_showdown() -> None:
    """Found live: a model seat whose provider kept failing through a
    showdown turn defaulted to `fold`, which the server correctly rejected
    as illegal_action — `fold`/`check` don't exist as options once
    `legal_actions` is `[show, muck]` (§3.1). `show` never forfeits a pot
    the seat might have won; `muck` always does — so it's the safer blind
    default of the two."""
    showdown_actions = [ActionSpec(type=ActionType.SHOW), ActionSpec(type=ActionType.MUCK)]
    assert default_action(showdown_actions).type == ActionType.SHOW


def test_default_action_still_prefers_check_when_offered() -> None:
    betting_actions = [ActionSpec(type=ActionType.CHECK), ActionSpec(type=ActionType.FOLD)]
    assert default_action(betting_actions).type == ActionType.CHECK


def test_default_action_falls_back_to_fold_when_neither_check_nor_show_is_offered() -> None:
    facing_a_bet = [ActionSpec(type=ActionType.FOLD), ActionSpec(type=ActionType.CALL, amount=100)]
    assert default_action(facing_a_bet).type == ActionType.FOLD

# -- test doubles -------------------------------------------------------------


class ScriptedProvider:
    """Always answers with a legal action: whatever `legal_actions` on the
    observation offers first that isn't a raise (the stub game never offers
    a `raise`, so this is always `check` or `fold`)."""

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, *, model: str, api_key: str, observation: Observation) -> RawDecision:
        self.calls += 1
        preferred = next((s for s in observation.legal_actions if s.type.value == "check"), None)
        spec = preferred or observation.legal_actions[0]
        return RawDecision(action_type=spec.type.value, to=spec.min_to, reasoning="scripted: taking the safe line")


class GarbageProvider:
    """Always answers with an action type that is never legal in the stub
    game, exercising the retry-then-default path."""

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, *, model: str, api_key: str, observation: Observation) -> RawDecision:
        self.calls += 1
        return RawDecision(action_type="raise", to=999999, reasoning="garbage: always illegal here")


class ErroringProvider:
    """Always raises `ProviderError`, as if every HTTP call failed."""

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, *, model: str, api_key: str, observation: Observation) -> RawDecision:
        self.calls += 1
        raise ProviderError("simulated transport failure")


class HangingProvider:
    """Blocks until released, to test the 15s decide-level timeout (with a
    tiny override) and that the room lock isn't held meanwhile."""

    def __init__(self) -> None:
        self.called = threading.Event()
        self.release = threading.Event()

    async def complete(self, *, model: str, api_key: str, observation: Observation) -> RawDecision:
        self.called.set()
        while not self.release.is_set():
            await asyncio.sleep(0.01)
        return RawDecision(action_type="check", to=None, reasoning="released")


# -- fixtures / helpers --------------------------------------------------------


def _rid() -> str:
    return str(uuid.uuid4())


def _client(provider_factory: Callable[[], Provider]) -> TestClient:
    app = create_app(allow_fixed_seed=True, model_provider_factory=provider_factory)
    return TestClient(app)


def _create_room(client: TestClient, seats: int = 4, rounds: int = 1) -> dict[str, object]:
    resp = client.post("/v1/rooms", json={"game": "stub", "seats": seats, "config": {"rounds": rounds}})
    assert resp.status_code == 201, resp.text
    result: dict[str, object] = resp.json()
    return result


def _claim_model(client: TestClient, room_id: str, invite: str, seat: int, name: str) -> dict[str, object]:
    resp = client.post(
        f"/v1/rooms/{room_id}/seats",
        json={
            "invite_token": invite,
            "seat": seat,
            "kind": "model",
            "display_name": name,
            "model": "test/scripted",
            "key_mode": "byok",
            "api_key": "sk-test-not-a-real-key",
        },
    )
    assert resp.status_code == 201, resp.text
    result: dict[str, object] = resp.json()
    return result


def _claim_human(client: TestClient, room_id: str, invite: str, seat: int, name: str) -> dict[str, object]:
    resp = client.post(
        f"/v1/rooms/{room_id}/seats",
        json={"invite_token": invite, "seat": seat, "kind": "human", "display_name": name},
    )
    assert resp.status_code == 201, resp.text
    result: dict[str, object] = resp.json()
    return result


def _wait_for_hand_complete(client: TestClient, room_id: str, timeout: float = 5.0) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = client.get(f"/v1/rooms/{room_id}/result")
        if resp.status_code == 200:
            result: dict[str, object] = resp.json()
            return result
        time.sleep(0.01)
    raise AssertionError(f"hand for room {room_id} never completed within {timeout}s")


# -- 20 hands, 4 model seats, zero illegal actions -----------------------------


def test_twenty_hands_four_model_seats_commit_zero_illegal_actions() -> None:
    provider = ScriptedProvider()
    client = _client(lambda: provider)
    with client:
        for hand in range(20):
            room = _create_room(client, seats=4, rounds=1)
            room_id = str(room["room_id"])
            invite = str(room["invite_token"])
            for seat in range(4):
                _claim_model(client, room_id, invite, seat, f"model-{seat}")
            start = client.post(f"/v1/rooms/{room_id}/start", json={"host_token": room["host_token"]})
            assert start.status_code == 200, start.text

            _wait_for_hand_complete(client, room_id)

            events_resp = client.get(f"/v1/rooms/{room_id}/events", params={"since": -1})
            assert events_resp.status_code == 200
            event_types = [ev["type"] for ev in events_resp.json()["events"]]
            assert "hand_complete" in event_types, f"hand {hand}: {event_types}"

    assert provider.calls > 0


# -- garbage provider: three strikes, then check-or-fold, never a crash -------


def test_garbage_provider_defaults_to_check_or_fold_not_a_crash() -> None:
    provider = GarbageProvider()
    client = _client(lambda: provider)
    with client:
        room = _create_room(client, seats=2, rounds=1)
        room_id = str(room["room_id"])
        invite = str(room["invite_token"])
        _claim_model(client, room_id, invite, 0, "model-0")
        _claim_human(client, room_id, invite, 1, "human-1")
        start = client.post(f"/v1/rooms/{room_id}/start", json={"host_token": room["host_token"]})
        assert start.status_code == 200, start.text

        deadline = time.monotonic() + 5.0
        action_taken = None
        while time.monotonic() < deadline and action_taken is None:
            events_resp = client.get(f"/v1/rooms/{room_id}/events", params={"since": -1})
            for ev in events_resp.json()["events"]:
                if ev["type"] == "action_taken" and ev["payload"]["seat"] == 0:
                    action_taken = ev
                    break
            if action_taken is None:
                time.sleep(0.01)

        assert action_taken is not None, "model seat never committed an action"
        assert action_taken["payload"]["action"]["type"] in ("check", "fold")
        # Every attempt (initial + 2 retries) hit the provider, all garbage.
        assert provider.calls == 3


def test_provider_transport_errors_also_default_not_crash() -> None:
    provider = ErroringProvider()
    client = _client(lambda: provider)
    with client:
        room = _create_room(client, seats=2, rounds=1)
        room_id = str(room["room_id"])
        invite = str(room["invite_token"])
        _claim_model(client, room_id, invite, 0, "model-0")
        _claim_human(client, room_id, invite, 1, "human-1")
        start = client.post(f"/v1/rooms/{room_id}/start", json={"host_token": room["host_token"]})
        assert start.status_code == 200, start.text

        deadline = time.monotonic() + 5.0
        action_taken = None
        while time.monotonic() < deadline and action_taken is None:
            events_resp = client.get(f"/v1/rooms/{room_id}/events", params={"since": -1})
            for ev in events_resp.json()["events"]:
                if ev["type"] == "action_taken" and ev["payload"]["seat"] == 0:
                    action_taken = ev
                    break
            if action_taken is None:
                time.sleep(0.01)

        assert action_taken is not None, "model seat never committed an action despite every provider call erroring"
        assert action_taken["payload"]["action"]["type"] in ("check", "fold")
        assert provider.calls == 3


# -- provider timeout -> default action ---------------------------------------


def test_provider_timeout_falls_back_to_default_action(monkeypatch: pytest.MonkeyPatch) -> None:
    from packages.agent_runtime import decide as decide_module

    # The task caps decide() at 15s in production; drop it to something a
    # unit test can afford without actually waiting 15 real seconds.
    monkeypatch.setattr(decide_module, "DEFAULT_TIMEOUT_SECONDS", 0.2)

    provider = HangingProvider()
    client = _client(lambda: provider)
    with client:
        room = _create_room(client, seats=2, rounds=1)
        room_id = str(room["room_id"])
        invite = str(room["invite_token"])
        _claim_model(client, room_id, invite, 0, "model-0")
        _claim_human(client, room_id, invite, 1, "human-1")
        start = client.post(f"/v1/rooms/{room_id}/start", json={"host_token": room["host_token"]})
        assert start.status_code == 200, start.text

        assert provider.called.wait(timeout=2.0), "provider was never called"
        provider.release.set()  # let the stalled call return so the test can clean up

        deadline = time.monotonic() + 3.0
        action_taken = None
        while time.monotonic() < deadline and action_taken is None:
            events_resp = client.get(f"/v1/rooms/{room_id}/events", params={"since": -1})
            for ev in events_resp.json()["events"]:
                if ev["type"] == "action_taken" and ev["payload"]["seat"] == 0:
                    action_taken = ev
                    break
            if action_taken is None:
                time.sleep(0.01)

        assert action_taken is not None, "model seat never committed an action after timing out"
        assert action_taken["payload"]["action"]["type"] in ("check", "fold")


# -- the room lock is not held during a provider call --------------------------


def test_room_lock_not_held_during_provider_call() -> None:
    provider = HangingProvider()
    client = _client(lambda: provider)
    with client:
        room = _create_room(client, seats=2, rounds=1)
        room_id = str(room["room_id"])
        invite = str(room["invite_token"])
        _claim_model(client, room_id, invite, 0, "model-0")
        human = _claim_human(client, room_id, invite, 1, "human-1")
        start = client.post(f"/v1/rooms/{room_id}/start", json={"host_token": room["host_token"]})
        assert start.status_code == 200, start.text

        assert provider.called.wait(timeout=2.0), "provider was never called"

        # If the room lock were held across the (still-blocked) provider
        # call, this lock-acquiring read would hang until `release` is set
        # below. It must return promptly instead.
        began = time.monotonic()
        view_resp = client.get(
            f"/v1/rooms/{room_id}/view", headers={"Authorization": f"Bearer {human['seat_token']}"}
        )
        elapsed = time.monotonic() - began
        assert view_resp.status_code == 200, view_resp.text
        assert elapsed < 1.0, f"GET /view took {elapsed:.2f}s — room lock may be held during the provider call"

        provider.release.set()


# -- BYOK key never leaks -------------------------------------------------------


def test_byok_key_appears_in_no_log_transcript_event_or_response_body(caplog: pytest.LogCaptureFixture) -> None:
    secret = "sk-live-do-not-leak-this-secret"
    provider = ScriptedProvider()
    client = _client(lambda: provider)
    with caplog.at_level(logging.DEBUG), client:
        room = _create_room(client, seats=2, rounds=1)
        room_id = str(room["room_id"])
        invite = str(room["invite_token"])
        claim_resp = client.post(
            f"/v1/rooms/{room_id}/seats",
            json={
                "invite_token": invite,
                "seat": 0,
                "kind": "model",
                "display_name": "model-0",
                "model": "test/scripted",
                "key_mode": "byok",
                "api_key": secret,
            },
        )
        assert claim_resp.status_code == 201, claim_resp.text
        assert secret not in claim_resp.text

        # Seat 1 is also a model seat (rather than human) so the hand
        # drives to completion on its own — this test only cares that
        # seat 0's BYOK secret never leaks anywhere along the way.
        _claim_model(client, room_id, invite, 1, "model-1")

        start = client.post(f"/v1/rooms/{room_id}/start", json={"host_token": room["host_token"]})
        assert start.status_code == 200, start.text
        assert secret not in start.text

        _wait_for_hand_complete(client, room_id)

        events_resp = client.get(f"/v1/rooms/{room_id}/events", params={"since": -1})
        assert secret not in events_resp.text

        result_resp = client.get(f"/v1/rooms/{room_id}/result")
        assert secret not in result_resp.text

    for record in caplog.records:
        assert secret not in record.getMessage()
