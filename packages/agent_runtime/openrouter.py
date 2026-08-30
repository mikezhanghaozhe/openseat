"""The OpenRouter provider (docs/MILESTONES.md M3: "OpenRouter only. One
key, one base URL, many models"). Structured output via tool-calling —
never regex on prose (the model's own words never touch action parsing;
only the tool-call's typed arguments do).
"""

from __future__ import annotations

import json

import httpx

from packages.agent_runtime.types import ProviderError, RawDecision
from packages.engine.types import ActionSpec, Observation

BASE_URL = "https://openrouter.ai/api/v1"

_TOOL_NAME = "submit_action"
_MAX_TOKENS = 300
# Below ModelSeat's overall 15s decide budget, leaving headroom for retry
# bookkeeping across up to 3 attempts within that same budget.
_REQUEST_TIMEOUT_SECONDS = 12.0


def _tool_schema(legal_actions: list[ActionSpec]) -> dict[str, object]:
    """The single tool the model may call, restricted to the action types
    `legal_actions` currently offers. `to` is still declared as an optional
    integer regardless of whether `raise` is offered — the schema only
    constrains `type`; the real bounds check happens in
    `packages.agent_runtime.policy.validate` against the *current*
    `min_to`/`max_to`, which this static schema can't express."""
    types = sorted({spec.type.value for spec in legal_actions})
    return {
        "type": "function",
        "function": {
            "name": _TOOL_NAME,
            "description": "Submit exactly one poker action for the current turn.",
            "parameters": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": types},
                    "to": {
                        "type": ["integer", "null"],
                        "description": "Total amount this seat's bet on this street becomes. Required for 'raise', omitted otherwise.",
                    },
                    "reasoning": {
                        "type": "string",
                        "description": "One or two sentences on why this action was chosen.",
                    },
                },
                "required": ["type", "reasoning"],
            },
        },
    }


def _as_dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ProviderError(f"expected an object in openrouter response, got {type(value).__name__}")
    return value


def _as_list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise ProviderError(f"expected an array in openrouter response, got {type(value).__name__}")
    return value


class OpenRouterProvider:
    """One `httpx.AsyncClient` per provider instance, reused across a room's
    model seats and turns. `transport` is the test injection point (an
    `httpx.MockTransport`) — never used to reach the real API in tests."""

    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None, base_url: str = BASE_URL) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, transport=transport, timeout=_REQUEST_TIMEOUT_SECONDS)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def complete(self, *, model: str, api_key: str, observation: Observation) -> RawDecision:
        """Send `observation.text` — already redacted, never a hand-built
        prompt from raw state (docs/MILESTONES.md M3) — as the sole user
        message, with `submit_action` as the only allowed tool call.

        Raises:
            ProviderError: on any transport failure, non-2xx status, or a
                response body that doesn't contain a well-formed tool call.
                Never raised for a well-formed call to an action that turns
                out to be illegal — that's `policy.PolicyViolation`, checked
                by the caller.
        """
        body: dict[str, object] = {
            "model": model,
            "messages": [{"role": "user", "content": observation.text}],
            "tools": [_tool_schema(observation.legal_actions)],
            "tool_choice": {"type": "function", "function": {"name": _TOOL_NAME}},
            "max_tokens": _MAX_TOKENS,
        }
        try:
            response = await self._client.post(
                "/chat/completions",
                json=body,
                headers={"Authorization": f"Bearer {api_key}"},
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"openrouter request failed: {exc}") from exc

        if response.status_code >= 400:
            raise ProviderError(f"openrouter returned HTTP {response.status_code}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderError(f"openrouter response was not JSON: {exc}") from exc

        data = _as_dict(payload)
        choices = _as_list(data.get("choices", []))
        if not choices:
            raise ProviderError("openrouter response had no choices")
        message = _as_dict(_as_dict(choices[0]).get("message", {}))
        tool_calls = _as_list(message.get("tool_calls", []))
        if not tool_calls:
            raise ProviderError("openrouter response made no tool call")
        function = _as_dict(_as_dict(tool_calls[0]).get("function", {}))
        raw_arguments = function.get("arguments")
        if not isinstance(raw_arguments, str):
            raise ProviderError("openrouter tool call arguments were not a string")
        try:
            arguments = _as_dict(json.loads(raw_arguments))
        except ValueError as exc:
            raise ProviderError(f"openrouter tool call arguments were not valid JSON: {exc}") from exc

        action_type = arguments.get("type")
        if not isinstance(action_type, str):
            raise ProviderError("openrouter tool call missing 'type'")
        to = arguments.get("to")
        if to is not None and not isinstance(to, int):
            raise ProviderError("openrouter tool call 'to' was not an integer")
        reasoning = arguments.get("reasoning")
        if not isinstance(reasoning, str):
            reasoning = ""
        return RawDecision(action_type=action_type, to=to, reasoning=reasoning)
