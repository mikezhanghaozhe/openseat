"""OpenRouter chat-completions client, tool-calling only.

Never regex on prose (per AGENTS.md instructions for this package): the
model is given exactly one tool, `submit_action`, whose JSON-schema enum is
built from that decision's `legal_actions` — the model literally cannot
name an action type that isn't already on offer. `to` is still checked
against `min_to`/`max_to` by the caller (`decide.py`); a schema enum can't
express "any integer in this range."
"""

from __future__ import annotations

import json
import os
import time

import httpx

from packages.engine.types import ActionSpec, ActionType, Observation

_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
_TOOL_NAME = "submit_action"

_SYSTEM_PROMPT = (
    "You are playing poker at a table. You will be given the current hand "
    "state as plain text. Call the submit_action tool exactly once with "
    "your chosen action. Only choose from the legal actions listed in the "
    "state text. For a raise, `to` is the total your bet on this street "
    "becomes, not the amount you're adding."
)


class ModelCallError(Exception):
    """The OpenRouter request failed, timed out, or returned a response
    this client could not parse into a tool call. Callers treat this the
    same as an invalid action — see decide.py."""


class RawToolCall:
    def __init__(
        self,
        action_type: str,
        to: int | None,
        *,
        prompt_tokens: int | None,
        completion_tokens: int | None,
    ) -> None:
        self.action_type = action_type
        self.to = to
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


def _tool_schema(legal_actions: list[ActionSpec]) -> dict[str, object]:
    types = sorted({spec.type.value for spec in legal_actions})
    return {
        "type": "function",
        "function": {
            "name": _TOOL_NAME,
            "description": "Submit one poker action for the current turn.",
            "parameters": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": types},
                    "to": {
                        "type": "integer",
                        "description": "Total bet-to amount for this street. Only used when type is 'raise'.",
                    },
                },
                "required": ["type"],
                "additionalProperties": False,
            },
        },
    }


def call_openrouter(
    observation: Observation,
    *,
    model: str,
    api_key: str | None,
    max_tokens: int,
    timeout_seconds: float,
) -> tuple[RawToolCall, int]:
    """One OpenRouter request. Returns the parsed tool call plus elapsed
    milliseconds. Raises `ModelCallError` on any failure — network error,
    timeout, missing/malformed tool call. Never logs `key`; never returns it."""
    key = api_key if api_key is not None else os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise ModelCallError("no OpenRouter API key configured")

    body = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": observation.text},
        ],
        "tools": [_tool_schema(observation.legal_actions)],
        "tool_choice": {"type": "function", "function": {"name": _TOOL_NAME}},
    }
    headers = {"Authorization": f"Bearer {key}"}

    started = time.monotonic()
    try:
        response = httpx.post(_OPENROUTER_URL, json=body, headers=headers, timeout=timeout_seconds)
    except httpx.HTTPError as exc:
        raise ModelCallError(f"openrouter request failed: {exc}") from exc
    elapsed_ms = int((time.monotonic() - started) * 1000)

    if response.status_code >= 400:
        raise ModelCallError(f"openrouter returned {response.status_code}")

    try:
        data = response.json()
        message = data["choices"][0]["message"]
        tool_calls = message["tool_calls"]
        call = next(tc for tc in tool_calls if tc["function"]["name"] == _TOOL_NAME)
        arguments = call["function"]["arguments"]
        parsed = json.loads(arguments) if isinstance(arguments, str) else arguments
        action_type = parsed["type"]
        to = parsed.get("to")
        if to is not None:
            to = int(to)
        usage = data.get("usage") or {}
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
    except (KeyError, IndexError, StopIteration, ValueError, TypeError) as exc:
        raise ModelCallError(f"could not parse openrouter tool call: {exc}") from exc

    if action_type not in {t.value for t in ActionType}:
        raise ModelCallError(f"model returned unknown action type {action_type!r}")

    return (
        RawToolCall(action_type, to, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
        elapsed_ms,
    )
