"""M3 model seats (docs/MILESTONES.md M3, docs/PROTOCOL.md §9): the
`ModelSeat.decide(observation) -> Action` machinery and its OpenRouter
provider. See `packages/room_server/store.py` for how a `Room` drives a
claimed model seat through this package and back into the same
`_commit_action` path a human uses (AGENTS.md invariant 4).
"""
