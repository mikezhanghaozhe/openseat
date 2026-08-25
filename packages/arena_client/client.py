"""`RoomClient` — the M1 REST surface (docs/PROTOCOL.md §6), nothing else.

M1 is REST only (§8: WebSocket arrives in M2 over the same contract) —
there is deliberately no `connect`/`ws` method here yet.

A fresh `httpx.Client` is created per `RoomClient` instance rather than
reusing a module-level client, so tests can inject a `MockTransport`
without any global state to reset between tests.
"""

from __future__ import annotations

import uuid
from types import TracebackType
from typing import Self

import httpx

from packages.arena_client import parse
from packages.arena_client.errors import error_for
from packages.arena_client.models import (
    ActionResult,
    EventsPage,
    HandResult,
    RoomCreated,
    RoomSummary,
    SeatClaimed,
    StartResult,
)
from packages.engine.types import Action, Observation

_BEARER_PREFIX = "Bearer "


class RoomClient:
    """Typed REST client for the M1 room-server API (§6). One instance per
    caller; use as a context manager or call `close()` explicitly to
    release the underlying `httpx.Client`."""

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 10.0,
    ) -> None:
        """
        Args:
            base_url: room-server base URL.
            transport: optional `httpx` transport override (e.g. `MockTransport` in tests).
            timeout: per-request timeout in seconds.
        """
        self._http = httpx.Client(base_url=base_url, transport=transport, timeout=timeout)

    def close(self) -> None:
        """Close the underlying HTTP client and its connection pool."""
        self._http.close()

    def __enter__(self) -> Self:
        """Context-manager entry: returns this client unchanged."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Context-manager exit: closes the underlying HTTP client regardless of `exc`."""
        self.close()

    # -- transport -----------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
        params: dict[str, int] | None = None,
    ) -> dict[str, object]:
        """Issue one HTTP request and return its parsed JSON object body.

        Args:
            method: HTTP method (e.g. "GET", "POST").
            path: request path, relative to `base_url`.
            json_body: optional JSON request body.
            headers: optional extra headers (e.g. the bearer token from `_bearer`).
            params: optional query parameters.

        Returns:
            The parsed JSON response body as a dict.

        Raises:
            ArenaApiError: (via `error_for`) if the response status is >= 400.
        """
        response = self._http.request(method, path, json=json_body, headers=headers, params=params)
        # A non-2xx response is not guaranteed to be one of our own JSON
        # error bodies — it could come from a proxy, a load balancer, or a
        # server that crashed before it could format one. Falling back to
        # the raw text rather than letting json.JSONDecodeError propagate
        # keeps every non-2xx response raising the same ArenaApiError type,
        # which is the whole point of centralizing error handling here.
        try:
            parsed = response.json()
        except ValueError:
            parsed = None
        body = parsed if isinstance(parsed, dict) else {}
        if response.status_code >= 400:
            if not body:
                body = {"reason": response.text}
            raise error_for(response.status_code, body)
        return parse.as_dict(parsed)

    @staticmethod
    def _bearer(token: str) -> dict[str, str]:
        """Build an `Authorization: Bearer <token>` header dict for `_request`."""
        return {"Authorization": _BEARER_PREFIX + token}

    # -- §6 endpoints ----------------------------------------------------------

    def create_room(
        self,
        game: str,
        seats: int,
        config: dict[str, object],
        *,
        seed: int | None = None,
    ) -> RoomCreated:
        """`POST /v1/rooms` — create a new room for `game` with `seats` slots
        and game-specific `config`.

        Args:
            game: game adapter id (e.g. `"holdem-nl"`).
            seats: total number of seats in the room.
            config: game-specific config dict, validated server-side against the adapter's schema.
            seed: optional fixed RNG seed; only accepted if the server has `allow_fixed_seed()` on.
        """
        body: dict[str, object] = {"game": game, "seats": seats, "config": config}
        if seed is not None:
            body["seed"] = seed
        return parse.parse_room_created(self._request("POST", "/v1/rooms", json_body=body))

    def claim_seat(
        self,
        room_id: str,
        invite_token: str,
        kind: str,
        display_name: str,
        *,
        seat: int | None = None,
    ) -> SeatClaimed:
        """`POST /v1/rooms/{room_id}/seats` — claim an open seat, returning
        the bearer `seat_token` used for all subsequent actions from it.

        Args:
            room_id: target room id.
            invite_token: the room's shared join token.
            kind: seat kind (e.g. "human" or "model").
            display_name: name to show for this seat.
            seat: specific seat index to claim; if omitted, the server assigns one.
        """
        body: dict[str, object] = {"invite_token": invite_token, "kind": kind, "display_name": display_name}
        if seat is not None:
            body["seat"] = seat
        data = self._request("POST", f"/v1/rooms/{room_id}/seats", json_body=body)
        return SeatClaimed(seat_token=parse.req_str(data, "seat_token"), seat_index=parse.req_int(data, "seat_index"))

    def get_room(self, room_id: str) -> RoomSummary:
        """`GET /v1/rooms/{room_id}` — fetch the room's lobby-level summary (no auth required)."""
        return parse.parse_room_summary(self._request("GET", f"/v1/rooms/{room_id}"))

    def start(self, room_id: str, host_token: str) -> StartResult:
        """`POST /v1/rooms/{room_id}/start` — host-only: begin play once all seats are filled.

        Args:
            room_id: target room id.
            host_token: bearer token proving host privileges.
        """
        data = self._request("POST", f"/v1/rooms/{room_id}/start", json_body={"host_token": host_token})
        return StartResult(
            hand_no=parse.req_int(data, "hand_no"),
            to_act=parse.opt_int(data, "to_act"),
            first_seq=parse.req_int(data, "first_seq"),
            last_seq=parse.req_int(data, "last_seq"),
        )

    def view(self, room_id: str, seat_token: str) -> Observation:
        """`Authorization: Bearer <seat_token>` — never a query string (§1:
        query strings are written to proxy logs, browser history, and
        `Referer` headers; a `seat_token` is a long-lived secret)."""
        data = self._request("GET", f"/v1/rooms/{room_id}/view", headers=self._bearer(seat_token))
        return parse.parse_observation(data)

    def act(
        self,
        room_id: str,
        seat_token: str,
        action: Action,
        *,
        table_talk: str | None = None,
        request_id: str | None = None,
    ) -> ActionResult:
        """A fresh `request_id` is generated per call unless the caller
        supplies one explicitly — §6's retry-safety guarantee depends on
        every *distinct* action getting its own id. A caller that wants to
        retry a dropped response passes the *same* `request_id` back in
        with the identical action; passing a fresh one for what was meant
        to be a retry defeats idempotency, and reusing one for a genuinely
        different action is exactly what produces `409
        request_id_conflict` (§6, §7) — this method does not protect
        against that misuse, since only the caller knows which case it's
        in."""
        rid = request_id if request_id is not None else str(uuid.uuid4())
        action_body: dict[str, object] = {"type": action.type.value}
        if action.to is not None:
            action_body["to"] = action.to
        body: dict[str, object] = {"seat_token": seat_token, "request_id": rid, "action": action_body}
        if table_talk is not None:
            body["table_talk"] = table_talk
        data = self._request("POST", f"/v1/rooms/{room_id}/actions", json_body=body)
        replayed_raw = data.get("replayed", False)
        assert isinstance(replayed_raw, bool)
        return ActionResult(
            first_seq=parse.req_int(data, "first_seq"),
            last_seq=parse.req_int(data, "last_seq"),
            accepted=parse.req_bool(data, "accepted"),
            replayed=replayed_raw,
        )

    def events(self, room_id: str, since: int = 0) -> EventsPage:
        """Pass `since=<the last_seq an earlier `act`/`start` call
        returned>` to fetch only what happened after that request — the
        pattern §6 describes for a polling client to know it has seen
        every consequence of its own request, without re-fetching events
        it already has."""
        data = self._request("GET", f"/v1/rooms/{room_id}/events", params={"since": since})
        events_raw = parse.as_list(data.get("events", []))
        return EventsPage(
            events=[parse.parse_event(parse.as_dict(item)) for item in events_raw],
            latest_seq=parse.req_int(data, "latest_seq"),
        )

    def result(self, room_id: str) -> HandResult:
        """`GET /v1/rooms/{room_id}/result` — fetch the outcome of the most recently completed hand."""
        return parse.parse_hand_result(self._request("GET", f"/v1/rooms/{room_id}/result"))
