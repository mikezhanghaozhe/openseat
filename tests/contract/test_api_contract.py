"""§6/§7 REST contract, plus the §10 checklist items that are only
observable over HTTP (token discipline, idempotency, room lifecycle,
config validation, determinism, event ordering). See conftest.py for why
every test here uses `game="holdem-nl"` and fails today at room creation.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from packages.engine.types import EventType
from tests.contract.conftest import (
    claim_seat,
    create_room,
    events,
    result,
    rid,
    room_summary,
    setup_room,
    start_room,
    submit_action,
    view,
)

pytestmark = pytest.mark.anyio


async def _first_to_act_token(client: httpx.AsyncClient, room_id: str, seat_tokens: list[str]) -> str:
    """The seat holding `seat_tokens[0]` is not necessarily first to act —
    in heads-up hold'em the button/small blind (seat index n-1, i.e. seat 1
    in a 2-seat room) acts first preflop, not seat 0 (docs/DECISIONS.md,
    "button is seat (n-1)"). `GET /view` is a pure read available to any
    claimed seat regardless of whose turn it is, so any token in
    `seat_tokens` can be used to look up the real `to_act` seat."""
    obs = (await view(client, room_id, seat_tokens[0])).json()
    to_act = obs["to_act"]
    assert to_act is not None, "expected a seat to be to_act right after /start"
    return seat_tokens[to_act]


# -- §10: GET /view token discipline -----------------------------------------


async def test_view_returns_401_for_missing_invite_host_and_foreign_seat_token(
    client: httpx.AsyncClient,
) -> None:
    """§10: "GET /view returns 401 for a missing token, an invite_token, a
    host_token, and a seat_token from another room — and never renders a
    default seat." All four cases in one test because they're one rule:
    /view accepts nothing but a seat_token that resolves to exactly one seat
    in *this* room (§6)."""
    ctx = await setup_room(client, n_seats=2)
    room_id = ctx["room_id"]

    missing = await view(client, room_id, None)
    assert missing.status_code == 401
    assert "you" not in missing.json(), "a 401 must never render a default seat's observation"

    via_invite = await view(client, room_id, ctx["invite_token"])
    assert via_invite.status_code == 401

    via_host = await view(client, room_id, ctx["host_token"])
    assert via_host.status_code == 401

    other = await setup_room(client, n_seats=2)
    via_foreign_seat = await view(client, room_id, other["seat_tokens"][0])
    assert via_foreign_seat.status_code == 401

    genuine = await view(client, room_id, ctx["seat_tokens"][0])
    assert genuine.status_code == 200


# -- §10: idempotency ----------------------------------------------------------


async def test_repeating_request_id_with_different_action_returns_409_conflict(
    client: httpx.AsyncClient,
) -> None:
    """§10: "Repeating a request_id with a different action body returns 409
    request_id_conflict." — §6/§7."""
    ctx = await setup_room(client, n_seats=2)
    room_id, seat_tokens = ctx["room_id"], ctx["seat_tokens"]
    actor_token = await _first_to_act_token(client, room_id, seat_tokens)
    request_id = rid()
    first = await submit_action(client, room_id, actor_token, {"type": "call"}, request_id=request_id)
    assert first.status_code == 200, first.text
    second = await submit_action(client, room_id, actor_token, {"type": "fold"}, request_id=request_id)
    assert second.status_code == 409
    assert second.json()["error"] == "request_id_conflict"


async def test_retrying_request_id_with_different_table_talk_does_not_conflict(
    client: httpx.AsyncClient,
) -> None:
    """§10: "Retrying a request_id with different table_talk but identical
    action does not conflict." Idempotency identity is (room_id, seat,
    request_id) compared on the canonical action only — table_talk is
    excluded (§6)."""
    ctx = await setup_room(client, n_seats=2)
    room_id, seat_tokens = ctx["room_id"], ctx["seat_tokens"]
    actor_token = await _first_to_act_token(client, room_id, seat_tokens)
    request_id = rid()
    first = await submit_action(
        client, room_id, actor_token, {"type": "call"}, request_id=request_id, table_talk="hi"
    )
    assert first.status_code == 200, first.text
    second = await submit_action(
        client, room_id, actor_token, {"type": "call"}, request_id=request_id, table_talk="bye"
    )
    assert second.status_code == 200
    assert second.json().get("replayed") is True


async def test_illegal_action_does_not_reserve_its_request_id(client: httpx.AsyncClient) -> None:
    """§10: "An illegal action does not reserve its request_id." A
    malformed/illegal request must not consume the id — the same id must
    still be usable for a legal follow-up (§6: "malformed, unauthorized,
    wrong-turn, and illegal requests reserve nothing")."""
    ctx = await setup_room(client, n_seats=2)
    room_id, seat_tokens = ctx["room_id"], ctx["seat_tokens"]
    actor_token = await _first_to_act_token(client, room_id, seat_tokens)
    request_id = rid()
    bad = await submit_action(
        client, room_id, actor_token, {"type": "raise", "to": -50}, request_id=request_id
    )
    assert bad.status_code == 409
    assert bad.json()["error"] == "illegal_action"
    good = await submit_action(client, room_id, actor_token, {"type": "call"}, request_id=request_id)
    assert good.status_code == 200
    assert "replayed" not in good.json()


async def test_actions_response_last_seq_equals_highest_seq_it_emitted(client: httpx.AsyncClient) -> None:
    """§10: "POST /actions response last_seq equals the highest seq that
    request emitted."""
    ctx = await setup_room(client, n_seats=2)
    room_id, seat_tokens = ctx["room_id"], ctx["seat_tokens"]
    actor_token = await _first_to_act_token(client, room_id, seat_tokens)
    resp = await submit_action(client, room_id, actor_token, {"type": "call"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    log = (await events(client, room_id, since=body["first_seq"] - 1)).json()["events"]
    caused = [e for e in log if body["first_seq"] <= e["seq"] <= body["last_seq"]]
    assert caused, "no events found in the claimed first_seq..last_seq range"
    assert max(e["seq"] for e in caused) == body["last_seq"]


# -- §10: room lifecycle --------------------------------------------------------


async def test_start_with_open_seat_returns_409_and_second_start_is_a_noop(
    client: httpx.AsyncClient,
) -> None:
    """§10: "/start with an open seat returns 409 seats_not_filled; a second
    /start is a no-op."""
    r = await create_room(client, seats=2)
    assert r.status_code == 201, r.text
    body = r.json()
    await claim_seat(client, body["room_id"], body["invite_token"], 0, "alice")
    incomplete = await start_room(client, body["room_id"], body["host_token"])
    assert incomplete.status_code == 409
    assert incomplete.json()["error"] == "seats_not_filled"

    await claim_seat(client, body["room_id"], body["invite_token"], 1, "bob")
    first_start = await start_room(client, body["room_id"], body["host_token"])
    assert first_start.status_code == 200, first_start.text
    second_start = await start_room(client, body["room_id"], body["host_token"])
    assert second_start.status_code == 200
    assert second_start.json() == first_start.json()

    log = (await events(client, body["room_id"], since=-1)).json()["events"]
    assert [e["type"] for e in log].count("hand_started") == 1, "a second /start must not deal again"


async def test_result_returns_200_after_close_and_409_before_hand_complete(
    client: httpx.AsyncClient,
) -> None:
    """§10: "GET /result returns 200 after the room closes, and 409 before
    hand_complete."""
    ctx = await setup_room(client, n_seats=2)
    room_id, seat_tokens = ctx["room_id"], ctx["seat_tokens"]

    before = await result(client, room_id)
    assert before.status_code == 409
    assert before.json()["error"] == "hand_in_progress"

    actor_token = await _first_to_act_token(client, room_id, seat_tokens)
    fold = await submit_action(client, room_id, actor_token, {"type": "fold"})
    assert fold.status_code == 200, fold.text

    after = await result(client, room_id)
    assert after.status_code == 200, after.text


# -- §10: config validation ------------------------------------------------------


async def test_seed_rejected_unless_fixed_seed_flag_is_on() -> None:
    """§10: "An explicit seed is rejected unless the fixed-seed flag is on."
    Uses its own app instance with the flag explicitly off, unlike every
    other test in this module (conftest sets ARENA_ALLOW_FIXED_SEED=1
    globally so determinism tests are possible)."""
    from packages.room_server.main import create_app

    app = create_app(allow_fixed_seed=False)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await create_room(c, seats=2, seed=42)
        assert resp.status_code == 400
        # asserted on `reason`, not just the status code: with no real game
        # registered yet, room creation already 400s for an unrelated cause
        # ("unknown game") — a bare status-code check would pass today for
        # the wrong reason, which is exactly what this suite must not do.
        assert "seed" in resp.json()["reason"].lower()


@pytest.mark.parametrize(
    "config",
    [
        {"sb": 50, "bb": 25, "ante": 0, "starting_stack": 5000, "turn_seconds": 30},  # sb >= bb
        {"sb": 50, "bb": 50, "ante": 0, "starting_stack": 5000, "turn_seconds": 30},  # sb == bb
        {"sb": 25, "bb": 50, "ante": 0, "starting_stack": 10, "turn_seconds": 30},  # starting_stack < bb
    ],
)
async def test_post_rooms_rejects_invalid_config_with_400(
    client: httpx.AsyncClient, config: dict[str, object]
) -> None:
    """§10: "POST /rooms with sb >= bb, seats out of range, or
    starting_stack < bb → 400." Config is validated before anything is
    created (§6). Asserted on `error == "invalid_config"` specifically
    (§7's documented code for "config failed adapter validation"), not just
    a bare 400 — with no real game registered yet, room creation already
    400s for an unrelated cause ("unknown game", error `bad_request`), and a
    status-code-only check would pass today without ever exercising real
    config validation."""
    resp = await create_room(client, seats=2, config=config)
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_config"


async def test_post_rooms_rejects_seats_out_of_range(client: httpx.AsyncClient) -> None:
    """§10: "POST /rooms with ... seats out of range ... → 400." Split from
    the config-only cases above because it's validated against the
    adapter's min_players/max_players, not config_schema — but §6 states
    seats-range violations also return `invalid_config` specifically, same
    as bad config values, so this asserts the same exact error code rather
    than a bare 400 (see the docstring above for why that distinction
    matters)."""
    too_few = await create_room(client, seats=1)
    assert too_few.status_code == 400
    assert too_few.json()["error"] == "invalid_config"
    too_many = await create_room(client, seats=100)
    assert too_many.status_code == 400
    assert too_many.json()["error"] == "invalid_config"


# -- §10: determinism (invariant 3) ----------------------------------------------


async def test_same_seed_same_actions_produce_identical_log_excluding_ts_and_deadline_ms() -> None:
    """§10 / invariant 3: "Two runs with the same seed produce identical
    logs once ts and action_required.deadline_ms are stripped." Both fields
    are stamped from wall-clock time by the room server, outside the
    adapter, and neither affects game outcome — `deadline_ms` joined `ts` in
    invariant 3's exclusion once the M2 turn clock started computing it from
    real time instead of carrying the adapters' inert `0` placeholder."""
    from packages.room_server.main import create_app

    async def run() -> list[dict[str, object]]:
        app = create_app(allow_fixed_seed=True)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await create_room(c, seats=2, seed=987654321)
            assert r.status_code == 201, r.text
            body = r.json()
            seat_tokens = []
            for i in range(2):
                sr = await claim_seat(c, body["room_id"], body["invite_token"], i, f"seat{i}")
                assert sr.status_code == 201, sr.text
                seat_tokens.append(sr.json()["seat_token"])
            started = await start_room(c, body["room_id"], body["host_token"])
            assert started.status_code == 200, started.text
            await submit_action(c, body["room_id"], seat_tokens[0], {"type": "fold"})
            log = (await events(c, body["room_id"], since=-1)).json()["events"]
            for e in log:
                del e["ts"]
                if e["type"] == "action_required":
                    del e["payload"]["deadline_ms"]
            return list(log)

    first = await run()
    second = await run()
    assert first == second


# -- §10: forward-compatibility --------------------------------------------------


async def test_no_m1_payload_contains_m2_reserved_fields(client: httpx.AsyncClient) -> None:
    """§10: "No M1 payload contains sitting_out, busted, room_complete, or
    seat_left." §4 reserves these for M2 and says they "must never appear in
    an M1 payload." Scanned across the full observable surface of a
    complete hand: room summary, every seat's view, the event log, and the
    result."""
    ctx = await setup_room(client, n_seats=2)
    room_id, seat_tokens = ctx["room_id"], ctx["seat_tokens"]
    await submit_action(client, room_id, seat_tokens[0], {"type": "fold"})

    surfaces = [
        (await room_summary(client, room_id)).text,
        (await view(client, room_id, seat_tokens[0])).text,
        (await view(client, room_id, seat_tokens[1])).text,
        (await events(client, room_id, since=-1)).text,
        (await result(client, room_id)).text,
    ]
    banned = ["sitting_out", "busted", "room_complete", "seat_left"]
    for text in surfaces:
        for token in banned:
            assert token not in text, f"M2-reserved token {token!r} leaked into an M1 payload"


async def test_canonical_setup_and_action_event_order_matches_protocol(client: httpx.AsyncClient) -> None:
    """§10: "Event order for each transition matches §5.0 exactly." Checked
    for the setup sequence and for the "all but one seat folded" transition
    (§5.0's table: action_taken → pot_awarded → hand_complete, with no
    board_dealt or showdown in between since nobody else contested)."""
    ctx = await setup_room(client, n_seats=2)
    room_id, seat_tokens = ctx["room_id"], ctx["seat_tokens"]

    log = (await events(client, room_id, since=-1)).json()["events"]
    setup_types = [e["type"] for e in log]
    assert setup_types[:2] == [EventType.ROOM_CREATED.value, EventType.SEAT_JOINED.value]
    assert setup_types.count(EventType.SEAT_JOINED.value) == 2
    hand_started_idx = setup_types.index(EventType.HAND_STARTED.value)
    assert setup_types[hand_started_idx:] == [
        EventType.HAND_STARTED.value,
        EventType.BLINDS_POSTED.value,
        EventType.HOLE_CARDS_DEALT.value,
        EventType.ACTION_REQUIRED.value,
    ]
    assert [e["seq"] for e in log] == list(range(len(log)))

    actor_token = await _first_to_act_token(client, room_id, seat_tokens)
    resp = await submit_action(client, room_id, actor_token, {"type": "fold"})
    assert resp.status_code == 200, resp.text
    caused = (await events(client, room_id, since=resp.json()["first_seq"] - 1)).json()["events"]
    caused = [e for e in caused if e["seq"] <= resp.json()["last_seq"]]
    assert [e["type"] for e in caused] == [
        EventType.ACTION_TAKEN.value,
        EventType.POT_AWARDED.value,
        EventType.HAND_COMPLETE.value,
    ], "uncontested-fold suffix must be exactly action_taken -> pot_awarded -> hand_complete, no showdown"


# -- §3.2/§8: showdown-phase turn-clock timeout ------------------------------


async def test_showdown_timeout_forces_muck_for_a_seat_that_cannot_win(client: httpx.AsyncClient) -> None:
    """§3.2/§8: "muck only if the seat cannot win any pot, otherwise show —
    never muck a winning hand on a timeout." This is `GameAdapter.can_win_now`
    (§9)'s entire reason for existing, and the flip side is just as real: a
    seat that CANNOT win must not have its cards silently published to the
    table and the event log just because it disconnected.

    Seed 4 (offline-verified against `HoldemAdapter` directly, see
    docs/DECISIONS.md) drives a real heads-up hand, checked down with no
    raises on every street, to a two-decision discretionary showdown: seat 0
    decides first and can win — shown here voluntarily, to expose its hand
    and make seat 1's situation determinate; seat 1 decides second and,
    once seat 0's hand is exposed, cannot win. Seat 1 is left to time out."""
    config = {"sb": 25, "bb": 50, "ante": 0, "starting_stack": 5000, "turn_seconds": 1}
    ctx = await setup_room(client, n_seats=2, config=config, seed=4)
    room_id, seat_tokens = ctx["room_id"], ctx["seat_tokens"]

    # check down every street — no raises, so nobody goes all-in and the
    # hand reaches a genuine discretionary (not all-in) showdown
    for _ in range(20):
        peek = (await view(client, room_id, seat_tokens[0])).json()
        seat = peek["to_act"]
        if peek["phase"] == "showdown" or seat is None:
            break
        # legal_actions is seat-relative — must view as the actual actor,
        # not always seat 0, or a seat 1 turn reads seat 0's (empty) options
        obs = (await view(client, room_id, seat_tokens[seat])).json()
        legal_types = {a["type"] for a in obs["legal_actions"]}
        action = {"type": "check"} if "check" in legal_types else {"type": "call"}
        resp = await submit_action(client, room_id, seat_tokens[seat], action)
        assert resp.status_code == 200, resp.text

    obs = (await view(client, room_id, seat_tokens[0])).json()
    assert obs["phase"] == "showdown", "expected a discretionary showdown to be reached"
    first_seat = obs["to_act"]
    assert first_seat == 0, "seed 4 was verified offline to give seat 0 the first showdown decision"

    show = await submit_action(client, room_id, seat_tokens[first_seat], {"type": "show"})
    assert show.status_code == 200, show.text

    obs = (await view(client, room_id, seat_tokens[1])).json()
    assert obs["phase"] == "showdown"
    assert obs["to_act"] == 1, "seat 1 must hold the second showdown decision"

    # deliberately never act for seat 1 — let its turn clock fire
    timed_out = None
    for _ in range(50):
        log = (await events(client, room_id, since=-1)).json()["events"]
        timed_out = next((e for e in log if e["type"] == "seat_timed_out"), None)
        if timed_out is not None:
            break
        await asyncio.sleep(0.1)

    assert timed_out is not None, "turn clock never fired a forced action for seat 1"
    assert timed_out["payload"]["seat"] == 1
    assert "forced_action" not in timed_out["payload"], "§5.1: showdown-phase timeouts must omit forced_action"

    outcome = (await result(client, room_id)).json()
    revealed_seats = {r["seat"] for r in outcome["showdown"]}
    assert 0 in revealed_seats, "seat 0 voluntarily showed"
    assert 1 not in revealed_seats, "seat 1's forced action must have been muck, not show"
