"""Structured logging for model-seat decisions and rule violations.

One rule: never pass an API key, a raw prompt, or a raw model response
into any log call here. Callers pass only the already-validated/derived
fields below.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("agent_runtime")


def log_violation(*, room_id: str, seat: int, model: str, attempt: int, reason: str) -> None:
    logger.warning(
        "model seat illegal/invalid action",
        extra={"room_id": room_id, "seat": seat, "model": model, "attempt": attempt, "reason": reason},
    )


def log_fallback(*, room_id: str, seat: int, model: str, action_type: str, reason: str) -> None:
    logger.warning(
        "model seat fell back to default action",
        extra={"room_id": room_id, "seat": seat, "model": model, "action_type": action_type, "reason": reason},
    )


def log_decision(
    *,
    room_id: str,
    seat: int,
    model: str,
    action_type: str,
    attempts: int,
    used_fallback: bool,
    latency_ms: int,
    prompt_tokens: int | None,
    completion_tokens: int | None,
) -> None:
    logger.info(
        "model seat decision",
        extra={
            "room_id": room_id,
            "seat": seat,
            "model": model,
            "action_type": action_type,
            "attempts": attempts,
            "used_fallback": used_fallback,
            "latency_ms": latency_ms,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        },
    )
