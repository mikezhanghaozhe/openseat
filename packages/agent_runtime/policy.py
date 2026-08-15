"""`decide(observation, config) -> Decision` — the validate/retry/fallback
policy this package owns.

Every action from every seat is untrusted (PROTOCOL.md invariant 4) — model
seats are no exception. `call_openrouter` returning *something* parseable is
not enough; the returned type/`to` must match this decision's own
`legal_actions` before it is ever handed to `RoomClient.act`. The room
server re-validates independently (invariant 4 is enforced there too), so
this check exists to keep illegal attempts off the wire and to drive the
retry/fallback policy, not as the only line of defense.
"""

from __future__ import annotations

import time

from packages.agent_runtime.logging import log_decision, log_fallback, log_violation
from packages.agent_runtime.openrouter import ModelCallError, call_openrouter
from packages.agent_runtime.types import Decision, ModelSeatConfig
from packages.engine.types import Action, ActionSpec, ActionType, Observation


def _find_spec(legal_actions: list[ActionSpec], action_type: ActionType) -> ActionSpec | None:
    for spec in legal_actions:
        if spec.type == action_type:
            return spec
    return None


def _validate(action_type_raw: str, to: int | None, legal_actions: list[ActionSpec]) -> Action | None:
    """Returns the validated `Action`, or `None` if it doesn't match any
    entry in `legal_actions`."""
    try:
        action_type = ActionType(action_type_raw)
    except ValueError:
        return None
    spec = _find_spec(legal_actions, action_type)
    if spec is None:
        return None
    if action_type == ActionType.RAISE:
        if spec.min_to is None or spec.max_to is None or to is None:
            return None
        if not (spec.min_to <= to <= spec.max_to):
            return None
        return Action(type=action_type, to=to)
    return Action(type=action_type, to=None)


def _default_action(legal_actions: list[ActionSpec]) -> Action:
    if _find_spec(legal_actions, ActionType.CHECK) is not None:
        return Action(type=ActionType.CHECK)
    return Action(type=ActionType.FOLD)


def decide(observation: Observation, config: ModelSeatConfig, *, room_id: str, seat: int) -> Decision:
    deadline = time.monotonic() + config.timeout_seconds
    attempts = 0
    max_attempts = 1 + config.max_retries

    while attempts < max_attempts:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        attempts += 1
        try:
            raw, latency_ms = call_openrouter(
                observation,
                model=config.model,
                api_key=config.api_key,
                max_tokens=config.max_tokens,
                timeout_seconds=min(remaining, config.timeout_seconds),
            )
        except ModelCallError as exc:
            log_violation(room_id=room_id, seat=seat, model=config.model, attempt=attempts, reason=str(exc))
            continue

        action = _validate(raw.action_type, raw.to, observation.legal_actions)
        if action is not None:
            log_decision(
                room_id=room_id,
                seat=seat,
                model=config.model,
                action_type=action.type.value,
                attempts=attempts,
                used_fallback=False,
                latency_ms=latency_ms,
                prompt_tokens=raw.prompt_tokens,
                completion_tokens=raw.completion_tokens,
            )
            return Decision(
                action=action,
                attempts=attempts,
                used_fallback=False,
                latency_ms=latency_ms,
                prompt_tokens=raw.prompt_tokens,
                completion_tokens=raw.completion_tokens,
            )

        log_violation(
            room_id=room_id,
            seat=seat,
            model=config.model,
            attempt=attempts,
            reason=f"illegal action: type={raw.action_type!r} to={raw.to!r}",
        )

    default = _default_action(observation.legal_actions)
    log_fallback(
        room_id=room_id,
        seat=seat,
        model=config.model,
        action_type=default.type.value,
        reason=f"no valid action after {attempts} attempt(s)",
    )
    total_latency_ms = int(max(0.0, config.timeout_seconds - max(0.0, deadline - time.monotonic())) * 1000)
    return Decision(
        action=default,
        attempts=attempts,
        used_fallback=True,
        latency_ms=total_latency_ms,
    )
