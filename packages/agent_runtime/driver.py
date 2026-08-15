"""Drives one model seat end to end: poll the room, decide, act.

M1 is REST-only (no WebSocket — PROTOCOL.md §8 is M2), so this polls
`GET /view` rather than reacting to a pushed `state` frame. One
`ModelSeatDriver` per seat; `arena_client.RoomClient` is the only thing it
talks to, matching the ownership map (`packages/arena-client` — used by
tests, model seats, MCP bridge).
"""

from __future__ import annotations

import logging
import time

from packages.agent_runtime.policy import decide
from packages.agent_runtime.types import ModelSeatConfig
from packages.arena_client import ArenaApiError, RoomClient
from packages.engine.types import Phase

logger = logging.getLogger("agent_runtime")


class ModelSeatDriver:
    def __init__(
        self,
        client: RoomClient,
        room_id: str,
        seat_token: str,
        seat_index: int,
        config: ModelSeatConfig,
        *,
        poll_seconds: float = 1.0,
    ) -> None:
        self._client = client
        self._room_id = room_id
        self._seat_token = seat_token
        self._seat_index = seat_index
        self._config = config
        self._poll_seconds = poll_seconds

    def run_until_hand_complete(self) -> None:
        while True:
            observation = self._client.view(self._room_id, self._seat_token)
            if observation.phase == Phase.HAND_COMPLETE:
                return
            if observation.to_act != self._seat_index:
                time.sleep(self._poll_seconds)
                continue

            decision = decide(
                observation,
                self._config,
                room_id=self._room_id,
                seat=self._seat_index,
            )
            try:
                self._client.act(self._room_id, self._seat_token, decision.action)
            except ArenaApiError as exc:
                # The turn pointer moved (or the action was rejected) between
                # our view() and act() — §7's contract is that a rejected
                # request never moves the turn pointer, so re-viewing and
                # deciding again is the correct recovery, not a crash.
                logger.warning(
                    "model seat act rejected, re-viewing",
                    extra={"room_id": self._room_id, "seat": self._seat_index, "error": exc.error.value},
                )
