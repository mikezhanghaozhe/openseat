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
    result = to_wire(obj)
    assert isinstance(result, dict)
    return result
