"""No-Limit Texas Hold'em `GameAdapter`, built on `pokerkit` 0.7.4.

The only package permitted to import `pokerkit` (AGENTS.md, docs/DECISIONS.md
"Rules engine is PokerKit, not our own"). Nothing outside `adapter.py` and
`cards.py` may see a raw `pokerkit.State` — `GameAdapter.view()` is the sole
conversion point into the protocol's `Observation` type.
"""
