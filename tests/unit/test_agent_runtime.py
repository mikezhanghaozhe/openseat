"""Unit tests for packages/agent_runtime's validate/retry/fallback policy.

`call_openrouter` is monkeypatched out entirely — no real network call, no
API key needed. What's under test is `decide()`'s handling of what comes
back: valid actions pass straight through, invalid ones get retried up to
`max_retries` times, and exhausting retries (or every call raising
`ModelCallError`) falls back to check-if-legal-else-fold. See
AGENTS.md's instructions for this package: "Validate the result against
legal_actions. On invalid, retry at most twice, then default to check if
legal, otherwise fold, and log the violation."
"""

from __future__ import annotations

import pytest

import packages.agent_runtime.policy as decide_module
from packages.agent_runtime.openrouter import ModelCallError, RawToolCall
from packages.agent_runtime.policy import decide
from packages.agent_runtime.types import ModelSeatConfig
from packages.engine.types import (
    ActionSpec,
    ActionType,
    Observation,
    Phase,
    SeatStatus,
    YouView,
)

_RAISE_LEGAL = [
    ActionSpec(type=ActionType.FOLD),
    ActionSpec(type=ActionType.CALL, amount=100),
    ActionSpec(type=ActionType.RAISE, min_to=200, max_to=1300),
]
_CHECK_LEGAL = [
    ActionSpec(type=ActionType.CHECK),
    ActionSpec(type=ActionType.RAISE, min_to=50, max_to=1300),
]
_FOLD_ONLY_LEGAL = [ActionSpec(type=ActionType.FOLD)]


def _observation(legal_actions: list[ActionSpec]) -> Observation:
    return Observation(
        protocol_version="0.1",
        seq=1,
        room_id="r_test",
        hand_no=1,
        phase=Phase.PREFLOP,
        to_act=0,
        button=0,
        you=YouView(
            seat=0, name="model-seat", hole=["Ah", "Kd"], stack=1000, committed_street=0, committed_hand=0,
            status=SeatStatus.ACTIVE,
        ),
        board=[],
        pots=[],
        pot_total=0,
        seats=[],
        to_call=100,
        min_raise_to=200,
        max_raise_to=1300,
        legal_actions=legal_actions,
        chat=[],
        text="irrelevant for these tests",
    )


def _config(**overrides: object) -> ModelSeatConfig:
    defaults: dict[str, object] = {"model": "test/model", "api_key": "sk-test", "timeout_seconds": 15.0}
    defaults.update(overrides)
    return ModelSeatConfig(**defaults)  # type: ignore[arg-type]


def test_valid_first_response_is_used_without_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def fake_call(observation, *, model, api_key, max_tokens, timeout_seconds):
        calls.append(1)
        return RawToolCall("raise", 300, prompt_tokens=10, completion_tokens=5), 120

    monkeypatch.setattr(decide_module, "call_openrouter", fake_call)
    result = decide(_observation(_RAISE_LEGAL), _config(), room_id="r_test", seat=0)

    assert result.action.type == ActionType.RAISE
    assert result.action.to == 300
    assert result.attempts == 1
    assert not result.used_fallback
    assert len(calls) == 1


def test_out_of_range_raise_is_rejected_and_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = iter(
        [
            (RawToolCall("raise", 50, prompt_tokens=None, completion_tokens=None), 10),  # below min_to
            (RawToolCall("call", None, prompt_tokens=None, completion_tokens=None), 10),
        ]
    )

    def fake_call(observation, *, model, api_key, max_tokens, timeout_seconds):
        return next(responses)

    monkeypatch.setattr(decide_module, "call_openrouter", fake_call)
    result = decide(_observation(_RAISE_LEGAL), _config(), room_id="r_test", seat=0)

    assert result.action.type == ActionType.CALL
    assert result.attempts == 2
    assert not result.used_fallback


def test_exhausting_retries_falls_back_to_check_when_legal(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_call(observation, *, model, api_key, max_tokens, timeout_seconds):
        # Always an action type that isn't on offer.
        return RawToolCall("fold", None, prompt_tokens=None, completion_tokens=None), 10

    monkeypatch.setattr(decide_module, "call_openrouter", fake_call)
    result = decide(_observation(_CHECK_LEGAL), _config(max_retries=2), room_id="r_test", seat=0)

    assert result.action.type == ActionType.CHECK
    assert result.used_fallback
    assert result.attempts == 3  # 1 initial + 2 retries


def test_fallback_folds_when_check_is_not_legal(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_call(observation, *, model, api_key, max_tokens, timeout_seconds):
        raise ModelCallError("network exploded")

    monkeypatch.setattr(decide_module, "call_openrouter", fake_call)
    result = decide(_observation(_FOLD_ONLY_LEGAL), _config(max_retries=2), room_id="r_test", seat=0)

    assert result.action.type == ActionType.FOLD
    assert result.used_fallback


def test_model_call_error_is_retried_like_an_invalid_action(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = {"n": 0}

    def fake_call(observation, *, model, api_key, max_tokens, timeout_seconds):
        attempts["n"] += 1
        if attempts["n"] < 2:
            raise ModelCallError("transient")
        return RawToolCall("call", None, prompt_tokens=None, completion_tokens=None), 10

    monkeypatch.setattr(decide_module, "call_openrouter", fake_call)
    result = decide(_observation(_RAISE_LEGAL), _config(), room_id="r_test", seat=0)

    assert result.action.type == ActionType.CALL
    assert not result.used_fallback
    assert result.attempts == 2
