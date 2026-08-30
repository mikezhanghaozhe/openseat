"""Validate a provider's `RawDecision` against an observation's actual
`legal_actions`, and the check-or-fold default used when nothing valid comes
back (docs/MILESTONES.md M3: "Validate -> retry <=2 -> default check/fold").

Never trust a provider's own claim about what's legal — `legal_actions` on
the `Observation` is the only source of truth (AGENTS.md invariant 4).
"""

from __future__ import annotations

from packages.agent_runtime.types import RawDecision
from packages.engine.types import Action, ActionSpec, ActionType


class PolicyViolation(Exception):
    """A `RawDecision` that doesn't correspond to a currently-legal action."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def validate(raw: RawDecision, legal_actions: list[ActionSpec]) -> Action:
    """Turn `raw` into an `Action`, or raise `PolicyViolation` if it isn't
    actually legal right now.

    Raises:
        PolicyViolation: unknown action type, a type not currently offered,
            or (for `raise`) a missing/out-of-bounds `to`.
    """
    try:
        action_type = ActionType(raw.action_type)
    except ValueError as exc:
        raise PolicyViolation(f"unknown action type {raw.action_type!r}") from exc

    spec = next((s for s in legal_actions if s.type == action_type), None)
    if spec is None:
        offered = sorted(s.type.value for s in legal_actions)
        raise PolicyViolation(f"{action_type.value!r} is not in legal_actions {offered}")

    if action_type != ActionType.RAISE:
        return Action(type=action_type)

    if raw.to is None:
        raise PolicyViolation("raise requires 'to'")
    if spec.min_to is None or spec.max_to is None:
        raise PolicyViolation("raise is not legal for this actor")
    if not (spec.min_to <= raw.to <= spec.max_to):
        raise PolicyViolation(f"raise to {raw.to} outside [{spec.min_to}, {spec.max_to}]")
    return Action(type=action_type, to=raw.to)


def default_action(legal_actions: list[ActionSpec]) -> Action:
    """The forced default when no valid decision can be obtained: check if
    legal, otherwise fold (docs/MILESTONES.md M3) — extended for the one
    decision space that isn't check/fold at all: showdown, where
    `legal_actions` is exactly `[show, muck]` (§3.1). `check`/`fold` aren't
    offered there, so falling straight to `fold` produced a genuine
    `illegal_action` rejection in practice (a real model seat hitting this
    live). `show` is the safer of the two blind defaults — it never
    forfeits a pot the seat might have won, where `muck` always does
    (mirrors the room server's own showdown-timeout bias in
    `Room._forced_showdown_action`, which has `can_win_now` to decide with;
    this default doesn't, so it always prefers the non-forfeiting option)."""
    offered = {spec.type for spec in legal_actions}
    if ActionType.CHECK in offered:
        return Action(type=ActionType.CHECK)
    if ActionType.SHOW in offered:
        return Action(type=ActionType.SHOW)
    return Action(type=ActionType.FOLD)
