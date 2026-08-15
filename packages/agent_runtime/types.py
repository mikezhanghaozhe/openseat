"""Config and result dataclasses for the OpenRouter model-seat runtime.

Nothing here knows about poker — `decide()` and `OpenRouterClient` work off
`Observation`/`Action`/`ActionSpec` from `packages/engine/types.py`, exactly
like `arena_client` does.
"""

from __future__ import annotations

from dataclasses import dataclass

from packages.engine.types import Action


@dataclass(frozen=True)
class ModelSeatConfig:
    """One model seat's decision policy.

    `api_key` defaults to `None`, which means "read `OPENROUTER_API_KEY` at
    call time" (see `openrouter.py`) — the house-key path. A BYOK key, once
    `room_server` grows one, is passed here explicitly and never touches the
    environment. Either way the key lives only in this object for the
    process lifetime; nothing in this package persists or logs it.
    """

    model: str
    api_key: str | None = None
    max_tokens: int = 300
    timeout_seconds: float = 15.0
    max_retries: int = 2  # additional attempts after the first, per protocol wording


@dataclass(frozen=True)
class Decision:
    """The result of `decide()`: the action to submit plus enough metadata
    to log a decision without ever including the prompt, the raw model
    response, or the API key."""

    action: Action
    attempts: int
    used_fallback: bool
    latency_ms: int
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
