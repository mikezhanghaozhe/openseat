"""§4 redaction + §10 leak checklist items, driven over HTTP with the real
game id (`holdem-nl`). None of this can pass until `packages/game-holdem`
exists and is wired into the room server's adapter registry — every test
here fails today at `setup_room`'s first assertion (`400 unknown game`).
"""

from __future__ import annotations

import json
import re

import httpx
import pytest

from tests.contract.conftest import (
    claim_seat,
    create_room,
    events,
    result,
    room_summary,
    setup_room,
    submit_action,
    view,
)

pytestmark = pytest.mark.anyio

_CARD_RE = re.compile(r"\b[2-9TJQKA][cdhs]\b")


def _keys(obj: object, found: set[str]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            found.add(k)
            _keys(v, found)
    elif isinstance(obj, list):
        for v in obj:
            _keys(v, found)


async def _surfaces(
    client: httpx.AsyncClient, room_id: str, seat_tokens: list[str]
) -> tuple[dict[int, str], str]:
    """Splits observable surfaces into (a) each seat's own `/view` — where
    that seat's own hole cards are legitimately present (§4: `you.hole`) —
    and (b) everything else: the public event log and the error bodies
    provoked by acting when it isn't a seat's turn, by sending a
    wildly-out-of-range raise, or by presenting an invalid token. No card
    may appear in (b), and a seat's own card may appear in (a) only for
    that seat's own entry."""
    own_views: dict[int, str] = {}
    for seat, tok in enumerate(seat_tokens):
        own_views[seat] = (await view(client, room_id, tok)).text
    shared_parts: list[str] = [(await events(client, room_id, since=-1)).text]
    for tok in seat_tokens:
        bad = await submit_action(client, room_id, tok, {"type": "raise", "to": 10**9})
        shared_parts.append(bad.text)
    shared_parts.append((await view(client, room_id, "sea_not-a-real-token")).text)
    return own_views, "\n".join(shared_parts)


async def test_no_hole_card_leak_across_any_observable_surface_all_phases_all_seats(
    client: httpx.AsyncClient,
) -> None:
    """§10 checklist, combined: "No deck or card appears in any view, event,
    or error body before `hand_complete`" + "`text` for seat i contains no
    card token belonging to any other live, un-mucked seat, at every phase"
    + "`GET /result` contains no hole card absent from the room's public
    event log". §4 funnels every redaction decision through one function
    (`GameAdapter.view`), so a leak anywhere downstream of it — a raw view,
    the event log, or an error body built from the wrong source — is the
    same bug wearing a different hat. This test checks the property
    continuously through every phase of a real hand, not once at the end,
    because a card that leaks for one street and is then legitimately
    exposed at showdown must still not have leaked *before* it was exposed.
    """
    ctx = await setup_room(client, n_seats=3, config={"sb": 5, "bb": 10, "ante": 0, "starting_stack": 200, "turn_seconds": 30})
    room_id = ctx["room_id"]
    seat_tokens: list[str] = ctx["seat_tokens"]

    hole_by_seat: dict[int, list[str]] = {}
    for seat, tok in enumerate(seat_tokens):
        v = (await view(client, room_id, tok)).json()
        hole_by_seat[seat] = v["you"]["hole"]
        assert hole_by_seat[seat], "test assumes hole cards are already dealt by the time /view is reachable"

    revealed: set[int] = set()

    for _ in range(500):
        log = (await events(client, room_id, since=-1)).json()["events"]
        for e in log:
            if e["type"] == "showdown":
                for r in e["payload"]["reveals"]:
                    revealed.add(r["seat"])
            if e["type"] == "hand_complete":
                return  # deck disclosure from here on is in-scope for the dedicated deck test, not this one

        own_views, shared_blob = await _surfaces(client, room_id, seat_tokens)
        for seat, cards in hole_by_seat.items():
            if seat in revealed:
                continue
            for card in cards:
                assert card not in shared_blob, (
                    f"seat {seat}'s hole card {card!r} leaked into a public/shared surface "
                    "(events, an error body, or an invalid-token view) before that seat was revealed"
                )
                for other_seat, own_view_text in own_views.items():
                    if other_seat == seat:
                        continue  # a seat's own view legitimately contains its own hole cards (§4)
                    assert card not in own_view_text, (
                        f"seat {seat}'s hole card {card!r} leaked into seat {other_seat}'s own /view"
                    )

        v0 = (await view(client, room_id, seat_tokens[0])).json()
        to_act = v0["to_act"]
        if to_act is None:
            break
        actor_view = (await view(client, room_id, seat_tokens[to_act])).json()
        legal_types = {a["type"] for a in actor_view["legal_actions"]}
        if "check" in legal_types:
            action = {"type": "check"}
        elif "call" in legal_types:
            action = {"type": "call"}
        elif "show" in legal_types:
            action = {"type": "show"}
        elif "muck" in legal_types:
            action = {"type": "muck"}
        else:
            break
        resp = await submit_action(client, room_id, seat_tokens[to_act], action)
        assert resp.status_code == 200, resp.text
    else:
        raise AssertionError("hand did not reach hand_complete within the iteration budget")


async def test_no_deck_or_card_appears_in_any_surface_before_hand_complete(
    client: httpx.AsyncClient,
) -> None:
    """§10: "No deck or card appears in any view, event, or error body before
    `hand_complete`." Distinct from the combined leak test above: this
    checks specifically for the full 52-card `deck` payload, which §10 says
    is disclosed exactly once, in `hand_complete`, and nowhere before it —
    not even in a malformed-request error body.

    Note: the original version of this test only called `/start` and then
    inspected the event log immediately — it never submitted a single
    action, so the hand could never reach `hand_complete` and the test
    failed on `assert saw_hand_complete` for that reason alone, not because
    of a leak. Fixed to actually drive the hand to completion, checking
    every event along the way, the same way the combined leak test above
    does."""
    ctx = await setup_room(client, n_seats=2)
    room_id = ctx["room_id"]
    seat_tokens: list[str] = ctx["seat_tokens"]

    saw_hand_complete = False
    for _ in range(200):
        log = (await events(client, room_id, since=-1)).json()["events"]
        for e in log:
            if e["type"] == "hand_complete":
                saw_hand_complete = True
                break
            assert "deck" not in json.dumps(e), f"deck leaked in {e['type']!r} event before hand_complete"
        if saw_hand_complete:
            break

        v0 = (await view(client, room_id, seat_tokens[0])).json()
        to_act = v0["to_act"]
        if to_act is None:
            break
        actor_view = (await view(client, room_id, seat_tokens[to_act])).json()
        legal_types = {a["type"] for a in actor_view["legal_actions"]}
        if "check" in legal_types:
            action = {"type": "check"}
        elif "call" in legal_types:
            action = {"type": "call"}
        elif "show" in legal_types:
            action = {"type": "show"}
        elif "muck" in legal_types:
            action = {"type": "muck"}
        else:
            break
        resp = await submit_action(client, room_id, seat_tokens[to_act], action)
        assert resp.status_code == 200, resp.text
    else:
        raise AssertionError("hand did not reach hand_complete within the iteration budget")

    assert saw_hand_complete, "hand never completed"

    bad = await submit_action(client, room_id, ctx["seat_tokens"][0], {"type": "raise", "to": -1})
    assert "deck" not in bad.text


async def test_room_seed_never_appears_in_any_client_facing_payload(client: httpx.AsyncClient) -> None:
    """§10: "`room_seed` never appears in any client-facing payload at any
    time." Checked structurally (no `seed`/`room_seed` key anywhere in any
    response), not by string search — the seed's numeric value could
    coincidentally match an unrelated field like a stack size."""
    ctx = await setup_room(client, n_seats=2, seed=123456789)
    room_id = ctx["room_id"]

    surfaces = [
        (await room_summary(client, room_id)).json(),
        (await view(client, room_id, ctx["seat_tokens"][0])).json(),
        (await events(client, room_id, since=-1)).json(),
    ]
    room_created = await create_room(client, seats=2)
    surfaces.append(room_created.json())

    found: set[str] = set()
    for surface in surfaces:
        _keys(surface, found)
    assert "seed" not in found
    assert "room_seed" not in found
    assert "master_seed" not in found


async def test_result_contains_no_hole_card_absent_from_the_public_event_log(
    client: httpx.AsyncClient,
) -> None:
    """§10: "`GET /result` contains no hole card absent from the room's
    public event log." `/result` is defined as a mechanical projection of
    already-public events (§6) — it must never read `state.hole_cards`
    directly, so any card it reports has to already be sitting in the log
    it's supposedly projecting."""
    ctx = await setup_room(client, n_seats=2, config={"sb": 5, "bb": 10, "ante": 0, "starting_stack": 20, "turn_seconds": 30})
    room_id = ctx["room_id"]

    # shove all-in preflop so both hands go to showdown and get exposed
    for tok in ctx["seat_tokens"]:
        actor = (await view(client, room_id, tok)).json()
        if actor["to_act"] is None:
            continue
        max_to = actor["max_raise_to"]
        resp = await submit_action(client, room_id, tok, {"type": "raise", "to": max_to})
        if resp.status_code != 200:
            await submit_action(client, room_id, tok, {"type": "call"})

    log_text = (await events(client, room_id, since=-1)).text
    res = (await result(client, room_id)).json()
    cards_in_result = set(_CARD_RE.findall(json.dumps(res)))
    for card in cards_in_result:
        assert card in log_text, f"/result exposed {card!r}, which never appeared in the public event log"


async def test_waiting_view_carries_no_card_data(client: httpx.AsyncClient) -> None:
    """Pins the pre-`/start` `Observation` (`GameAdapter.waiting_view`, moved
    out of `store.py` to keep `packages/room_server/` at zero `Observation`
    construction sites — docs/DECISIONS.md "room-server: zero `Observation`
    construction sites"). No `GameState` exists yet at this point, so nothing
    resembling a card may appear anywhere in the view: `you.hole` must be
    empty, `board` must be empty, `pots` must be empty, and no `SeatView` may
    carry a `revealed` entry. A later change that starts filling this view in
    with real state before `/start` would trip this test first."""
    room = await create_room(client, seats=2)
    assert room.status_code == 201, room.text
    body = room.json()
    room_id = body["room_id"]

    seat0 = await claim_seat(client, room_id, body["invite_token"], 0, "alice")
    assert seat0.status_code == 201, seat0.text
    seat1 = await claim_seat(client, room_id, body["invite_token"], 1, "bob")
    assert seat1.status_code == 201, seat1.text

    v = (await view(client, room_id, seat0.json()["seat_token"])).json()

    assert v["phase"] == "waiting"
    assert v.get("to_act") is None
    assert v["you"]["hole"] == []
    assert v["board"] == []
    assert v["pots"] == []
    assert v["pot_total"] == 0
    assert len(v["seats"]) == 2
    for sv in v["seats"]:
        assert "revealed" not in sv or sv["revealed"] is None
        assert not _CARD_RE.search(json.dumps(sv))
