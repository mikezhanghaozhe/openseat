"""FastAPI room server — M1 REST surface. See docs/PROTOCOL.md §6.

This package handles `Observation` and `Event` only, as returned by
`GameAdapter.view`/`GameAdapter.apply`. It never imports or holds a raw
`GameState` — see `packages/room_server/adapter.py` for how that boundary is
enforced by construction.
"""
