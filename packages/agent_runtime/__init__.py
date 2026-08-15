"""OpenRouter-backed decision policy for `kind: "model"` seats (docs/PROTOCOL.md §6, §3).

Talks to the room only through `packages.arena_client.RoomClient` — same
boundary every other caller of the REST surface uses. `decide()` is the
validate/retry/fallback policy; `ModelSeatDriver` is the poll-and-act loop
around it.
"""

from packages.agent_runtime.driver import ModelSeatDriver
from packages.agent_runtime.openrouter import ModelCallError
from packages.agent_runtime.policy import decide
from packages.agent_runtime.types import Decision, ModelSeatConfig

__all__ = [
    "Decision",
    "ModelCallError",
    "ModelSeatConfig",
    "ModelSeatDriver",
    "decide",
]
