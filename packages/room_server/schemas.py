"""Pydantic request bodies for the §6 REST endpoints. Response bodies are
plain `dict[str, object]` built via `serialize.to_wire` — see main.py."""

from __future__ import annotations

from pydantic import BaseModel


class CreateRoomRequest(BaseModel):
    game: str
    seats: int
    config: dict[str, object] = {}
    seed: int | None = None


class ClaimSeatRequest(BaseModel):
    invite_token: str
    seat: int | None = None
    kind: str
    display_name: str
    # M3 model seats (docs/MILESTONES.md M3) — only meaningful when kind == "model".
    model: str | None = None
    key_mode: str | None = None  # "house" | "byok"
    api_key: str | None = None  # BYOK only; never echoed back in any response


class StartRequest(BaseModel):
    host_token: str


class ActionIn(BaseModel):
    type: str
    to: int | None = None


class ActionRequest(BaseModel):
    seat_token: str
    request_id: str
    action: ActionIn
    table_talk: str | None = None
