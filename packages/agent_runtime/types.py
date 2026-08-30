"""Shared types for the M3 model-seat machinery.

No provider- or policy-specific logic lives here — just the shapes they pass
between each other, mirroring how `packages/engine/types.py` is the
dependency-free type module every other package imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from packages.engine.types import Action, Observation


@dataclass(frozen=True)
class ModelSeatSpec:
    """A `kind: "model"` seat's claim-time configuration (docs/MILESTONES.md
    M3's `POST /rooms/{id}/seats` fields).

    `api_key` is only ever the BYOK key supplied at claim time — the house
    key (from `OPENROUTER_API_KEY`) never passes through this dataclass; see
    `packages/agent_runtime/driver.resolve_api_key`. Either way, the
    *resolved* key is held only in the caller's memory for the room's
    lifetime (AGENTS.md invariant 6) — never stored here alongside anything
    that gets logged or serialized.
    """

    model: str
    key_mode: str  # "house" | "byok"
    api_key: str | None = None  # BYOK only; None for key_mode == "house"


@dataclass(frozen=True)
class RawDecision:
    """A provider's unvalidated answer for one turn: what it claims the
    action is, plus its stated reasoning. Never trusted until
    `packages.agent_runtime.policy.validate` checks it against the
    observation's actual `legal_actions` (AGENTS.md invariant 4)."""

    action_type: str
    to: int | None
    reasoning: str


@dataclass(frozen=True)
class Decision:
    """The result `ModelSeat.decide` hands back to its caller: the action to
    submit (already validated against `legal_actions`, or a policy default)
    plus the reasoning to record, and whether this was a fallback rather
    than the provider's own valid choice."""

    action: Action
    reasoning: str
    violated: bool = False


class ProviderError(Exception):
    """Raised by a `Provider` when it fails to produce a usable response —
    a transport error, a non-2xx status, or a malformed body. Never raised
    for a well-formed-but-illegal action; that's `policy.PolicyViolation`,
    a distinct failure mode with a distinct retry story."""


class Provider(Protocol):
    """One provider call for one turn. Implementations must not block the
    caller's event loop for longer than they can help — `ModelSeat.decide`
    is what enforces the overall 15s cap, not this protocol."""

    async def complete(self, *, model: str, api_key: str, observation: Observation) -> RawDecision: ...
