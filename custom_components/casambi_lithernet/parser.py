"""JSON payloads to state objects.

Pure functions, no logging and no exceptions: a payload
that is not usable produces ``None`` and the caller decides what to say about
it. The gateway logs the first bad message per topic on warning and the rest
on debug (docs/DESIGN.md, "Fehlerbehandlung").

Field names and the gateway's own spellings live in :mod:`.const`; nothing in
here writes a key literally.
"""

from __future__ import annotations

import json
from math import isfinite
from typing import Any

from .const import (
    KEY_ACTIVE,
    KEY_AMBIENT_TEMPERATURE,
    KEY_BATTERY_LEVEL,
    KEY_CCT_LEVEL,
    KEY_CONDITION,
    KEY_GENERAL_FAILURE,
    KEY_LAST_CHANGE,
    KEY_LAST_LEVEL,
    KEY_LEVEL,
    KEY_NODE_TYPE,
    KEY_ONLINE,
    KEY_OVERHEATING,
    KEY_PRIORITY,
    KEY_SCENE,
    KEY_VERTICAL,
)
from .state import AggregateValues, SceneValues, UnitProperties, UnitValues


def decode(raw: str | bytes) -> dict[str, Any] | None:
    """Turn a raw payload into a dictionary, or ``None`` if it is not one.

    Anything the gateway sends that is not a JSON object - an empty retained
    message, a bare number, a list - is not a state message.
    """
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def parse_unit_values(raw: str | bytes) -> UnitValues | None:
    """Parse a ``poll_device/<id>/values`` payload.

    Without a ``level`` the message says nothing about the luminaire, so it
    counts as invalid rather than as "off".
    """
    data = decode(raw)
    if data is None or KEY_LEVEL not in data:
        return None
    level = _as_int(data.get(KEY_LEVEL))
    if level is None:
        return None
    return UnitValues(
        level=level,
        last_level=_int_field(data, KEY_LAST_LEVEL),
        cct_level=_int_field(data, KEY_CCT_LEVEL),
        scene=_int_field(data, KEY_SCENE),
        vertical=_int_field(data, KEY_VERTICAL),
        last_change=_int_field(data, KEY_LAST_CHANGE),
    )


def parse_unit_properties(raw: str | bytes) -> UnitProperties | None:
    """Parse a ``poll_device/<id>/propertys`` payload.

    ``online`` is the field that has to be there; the diagnostic values below
    it are optional and default to zero, because older firmware omits some.
    """
    data = decode(raw)
    if data is None or KEY_ONLINE not in data:
        return None
    online = _as_int(data.get(KEY_ONLINE))
    if online is None:
        return None
    return UnitProperties(
        online=bool(online),
        node_type=_int_field(data, KEY_NODE_TYPE),
        priority_raw=_int_field(data, KEY_PRIORITY),
        condition_raw=_int_field(data, KEY_CONDITION),
        ambient_temperature=_int_field(data, KEY_AMBIENT_TEMPERATURE),
        battery_level=_int_field(data, KEY_BATTERY_LEVEL),
        overheating=_int_field(data, KEY_OVERHEATING),
        general_failure=_int_field(data, KEY_GENERAL_FAILURE),
        last_change=_int_field(data, KEY_LAST_CHANGE),
    )


def parse_aggregate_values(raw: str | bytes) -> AggregateValues | None:
    """Parse ``poll_group/<n>``, ``poll_broadcast`` or ``poll_ungrouped``.

    All three carry the same fields; ``level`` is an average across the member
    luminaires (docs/DESIGN.md, "Topics").
    """
    data = decode(raw)
    if data is None or KEY_LEVEL not in data:
        return None
    level = _as_int(data.get(KEY_LEVEL))
    if level is None:
        return None
    return AggregateValues(
        level=level,
        last_level=_int_field(data, KEY_LAST_LEVEL),
        cct_level=_int_field(data, KEY_CCT_LEVEL),
        vertical=_int_field(data, KEY_VERTICAL),
        last_change=_int_field(data, KEY_LAST_CHANGE),
    )


def parse_scene_values(raw: str | bytes) -> SceneValues | None:
    """Parse a ``poll_scene/<n>`` payload.

    A scene reports ``active`` and the level it was called with; both are
    needed, because a scene that is not active still reports its level.
    """
    data = decode(raw)
    if data is None or KEY_ACTIVE not in data or KEY_LEVEL not in data:
        return None
    active = _as_int(data.get(KEY_ACTIVE))
    level = _as_int(data.get(KEY_LEVEL))
    if active is None or level is None:
        return None
    return SceneValues(
        active=bool(active),
        level=level,
        last_change=_int_field(data, KEY_LAST_CHANGE),
    )


def _int_field(data: dict[str, Any], key: str, default: int = 0) -> int:
    """Read an optional numeric field, using the default when it is unusable.

    Optional fields are missing on older firmware; a broken one must not cost
    the whole message, because the fields that matter were already checked.
    """
    value = _as_int(data.get(key))
    return default if value is None else value


def _as_int(value: Any) -> int | None:
    """Read a numeric payload field, or ``None`` when it is not numeric.

    Booleans and floats are accepted because the gateway is not consistent
    about them; strings and objects are not, because they mean the payload is
    something else entirely.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        # Infinity and NaN reach int() as an OverflowError or a ValueError,
        # which would escape into the MQTT callback and leave the message
        # uncounted and unlogged.
        if not isfinite(value):
            return None
        return int(value)
    return None
