"""Dataclass → JSON-safe dict conversion for wire responses.

One rule covers every "omitted, not null" case in docs/PROTOCOL.md §4/§5:
a dataclass field whose value is `None` is dropped from the output entirely
rather than serialized as `null`. This is deliberate and generic — see
docs/DECISIONS.md.
"""

from __future__ import annotations

import dataclasses
from enum import Enum


def to_wire(obj: object) -> object:
    """Recursively convert a dataclass/Enum/list/tuple/dict tree into plain
    JSON-safe Python values (dict/list/str/int/float/bool/None).

    Args:
        obj: any value — a dataclass instance, Enum member, list/tuple,
            dict, or plain JSON-safe scalar; nested structures are walked
            recursively.

    Returns:
        The JSON-safe equivalent. Dataclass fields whose value is `None`
        are omitted from the output dict entirely (see module docstring);
        Enum members become their `.value`; everything else is recursed
        into or returned unchanged.
    """
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        result: dict[str, object] = {}
        for f in dataclasses.fields(obj):
            value = getattr(obj, f.name)
            if value is None:
                continue
            result[f.name] = to_wire(value)
        return result
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, (list, tuple)):
        return [to_wire(v) for v in obj]
    if isinstance(obj, dict):
        return {k: to_wire(v) for k, v in obj.items()}
    return obj


def to_wire_dict(obj: object) -> dict[str, object]:
    """`to_wire`, narrowed to a dict return for callers (e.g. FastAPI route
    handlers) that need a JSON object body, not any JSON-safe value.

    Args:
        obj: a dataclass instance expected to serialize to a dict at the
            top level (any other input that produces a non-dict result
            trips the assertion).
    """
    result = to_wire(obj)
    assert isinstance(result, dict)
    return result
