"""`ModelSeat.decide(observation) -> Decision` (docs/PROTOCOL.md §9's
`ModelSeat` interface, docs/MILESTONES.md M3): one provider call, validated
against `legal_actions`, retried at most twice on garbage, capped at 15s
total and falling back to check-or-fold on either exhaustion or timeout.

Every violation and fallback is logged (never the resolved API key —
AGENTS.md invariant 6) so the illegal-action rate is visible per model
(docs/MILESTONES.md M3 gate: "Illegal-action rate emitted per model").
"""

from __future__ import annotations

import asyncio
import logging

from packages.agent_runtime.policy import PolicyViolation, default_action, validate
from packages.agent_runtime.types import (
    Decision,
    ModelSeatSpec,
    Provider,
    ProviderError,
)
from packages.engine.types import Observation

logger = logging.getLogger("agent_runtime.decide")

MAX_ATTEMPTS = 3  # the initial attempt plus at most 2 retries
DEFAULT_TIMEOUT_SECONDS = 15.0


class ModelSeat:
    """One claimed model seat's decision-maker. `api_key` is the already-
    resolved house/BYOK key (see `driver.resolve_api_key`) — held here only
    for the room's lifetime, never logged, never returned from `decide`."""

    def __init__(
        self,
        spec: ModelSeatSpec,
        provider: Provider,
        api_key: str,
        *,
        timeout_seconds: float | None = None,
    ) -> None:
        self._spec = spec
        self._provider = provider
        self._api_key = api_key
        # Resolved from the module-level default at construction time, not
        # bound into the signature at import time — so tests can lower
        # `DEFAULT_TIMEOUT_SECONDS` via monkeypatch before a seat is claimed.
        self._timeout_seconds = timeout_seconds if timeout_seconds is not None else DEFAULT_TIMEOUT_SECONDS

    async def decide(self, observation: Observation) -> Decision:
        """Cap the whole attempt-and-retry sequence at `timeout_seconds`;
        a slow provider falls back to the default action rather than
        stalling the seat's turn indefinitely."""
        try:
            return await asyncio.wait_for(self._decide_with_retries(observation), timeout=self._timeout_seconds)
        except TimeoutError:
            logger.warning(
                "model seat %d (%s) timed out after %.0fs; defaulting",
                observation.you.seat,
                self._spec.model,
                self._timeout_seconds,
            )
            return Decision(
                action=default_action(observation.legal_actions),
                reasoning="provider timed out; defaulted to check-or-fold",
                violated=True,
            )

    async def _decide_with_retries(self, observation: Observation) -> Decision:
        last_reason = "no attempts made"
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                raw = await self._provider.complete(
                    model=self._spec.model, api_key=self._api_key, observation=observation
                )
            except ProviderError as exc:
                last_reason = str(exc)
                logger.warning(
                    "model seat %d (%s) provider error on attempt %d/%d: %s",
                    observation.you.seat,
                    self._spec.model,
                    attempt,
                    MAX_ATTEMPTS,
                    last_reason,
                )
                continue

            try:
                action = validate(raw, observation.legal_actions)
            except PolicyViolation as exc:
                last_reason = exc.reason
                logger.warning(
                    "model seat %d (%s) illegal action on attempt %d/%d: %s",
                    observation.you.seat,
                    self._spec.model,
                    attempt,
                    MAX_ATTEMPTS,
                    last_reason,
                )
                continue

            logger.info(
                "model seat %d (%s) decided %s on attempt %d/%d: %s",
                observation.you.seat,
                self._spec.model,
                action.type.value if action.to is None else f"{action.type.value} to {action.to}",
                attempt,
                MAX_ATTEMPTS,
                raw.reasoning,
            )
            return Decision(action=action, reasoning=raw.reasoning)

        logger.error(
            "model seat %d (%s) exhausted %d attempts; defaulting to check-or-fold: %s",
            observation.you.seat,
            self._spec.model,
            MAX_ATTEMPTS,
            last_reason,
        )
        return Decision(
            action=default_action(observation.legal_actions),
            reasoning=f"defaulted after {MAX_ATTEMPTS} invalid provider attempts: {last_reason}",
            violated=True,
        )
