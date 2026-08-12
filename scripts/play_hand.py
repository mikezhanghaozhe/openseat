#!/usr/bin/env python3
"""Drive four seats through one hand of `holdem-nl` to completion over HTTP
only, using `packages.arena_client.RoomClient` — the M1 gate (docs/
MILESTONES.md): "scripts/play_hand.sh drives 4 seats to showdown through
HTTP only."

Exits 0 only if the hand reaches `hand_complete` and `/result` reads back
cleanly. Any unexpected HTTP status, illegal action, or non-terminating
hand is a non-zero exit with a message on stderr — this script is the gate,
not a demo, so it must fail loudly rather than print a warning and exit 0.
"""

from __future__ import annotations

import os
import sys

from packages.arena_client import ArenaApiError, IllegalActionError, RoomClient
from packages.engine.types import Action, ActionType

SEATS = 4
CONFIG: dict[str, object] = {"sb": 25, "bb": 50, "ante": 0, "starting_stack": 5000, "turn_seconds": 30}
MAX_ACTIONS = 200  # generous bound for 4 seats through one hand; a hand that needs more is a bug


def _pick_action(legal_types: set[ActionType]) -> Action:
    if ActionType.CHECK in legal_types:
        return Action(type=ActionType.CHECK)
    if ActionType.CALL in legal_types:
        return Action(type=ActionType.CALL)
    if ActionType.SHOW in legal_types:
        return Action(type=ActionType.SHOW)
    if ActionType.FOLD in legal_types:
        return Action(type=ActionType.FOLD)
    raise RuntimeError(f"no action this driver knows how to pick from {legal_types}")


def play(base_url: str) -> None:
    with RoomClient(base_url) as client:
        room = client.create_room("holdem-nl", SEATS, CONFIG)
        print(f"room created: {room.room_id}")

        seat_tokens: list[str] = []
        for i in range(SEATS):
            claimed = client.claim_seat(room.room_id, room.invite_token, "agent", f"seat{i}", seat=i)
            seat_tokens.append(claimed.seat_token)
        print(f"claimed {SEATS} seats")

        started = client.start(room.room_id, room.host_token)
        print(f"hand {started.hand_no} started, to_act={started.to_act}, seq {started.first_seq}..{started.last_seq}")

        last_seq = started.last_seq
        for step in range(1, MAX_ACTIONS + 1):
            obs = client.view(room.room_id, seat_tokens[0])
            if obs.phase.value == "hand_complete":
                break
            to_act = obs.to_act
            if to_act is None:
                raise RuntimeError(f"no seat to act but phase is {obs.phase.value!r} — stuck")

            actor_obs = client.view(room.room_id, seat_tokens[to_act])
            legal_types = {spec.type for spec in actor_obs.legal_actions}
            action = _pick_action(legal_types)

            try:
                result = client.act(room.room_id, seat_tokens[to_act], action)
            except IllegalActionError as exc:
                raise RuntimeError(
                    f"step {step}: seat {to_act} action {action} rejected as illegal: {exc.reason} "
                    f"(server's legal_actions: {exc.legal_actions})"
                ) from exc

            print(f"step {step}: seat {to_act} -> {action.type.value}" + (f" to {action.to}" if action.to else ""))

            # §6: passing since=last_seq is how a polling client confirms it
            # has seen every consequence of its own request.
            page = client.events(room.room_id, since=result.last_seq)
            if page.events:
                raise RuntimeError(f"events reported after last_seq={result.last_seq}, which should be impossible: {page.events}")
            last_seq = result.last_seq
        else:
            raise RuntimeError(f"hand did not complete within {MAX_ACTIONS} actions")

        print(f"hand complete at seq {last_seq}")
        result_data = client.result(room.room_id)
        print(f"hand_no={result_data.hand_no} final_stacks={result_data.final_stacks}")
        for pot in result_data.pots:
            awards = ", ".join(f"seat {a.seat}: {a.amount}" for a in pot.awards)
            print(f"  pot {pot.index} ({pot.reason.value}, {pot.amount}): {awards}")
        if result_data.showdown:
            for reveal in result_data.showdown:
                print(f"  seat {reveal.seat} showed {reveal.hole} — {reveal.description}")


def main() -> int:
    base_url = os.environ.get("ARENA_BASE_URL", "http://127.0.0.1:8000")
    try:
        play(base_url)
    except ArenaApiError as exc:
        print(f"FAILED: server returned {exc.status_code} {exc.error.value}: {exc.reason}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 — this script's job is to turn any failure into a clean non-zero exit
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    print("OK: hand reached showdown/completion through HTTP only")
    return 0


if __name__ == "__main__":
    sys.exit(main())
