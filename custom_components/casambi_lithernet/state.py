"""Immutable state objects parsed from gateway messages.

:mod:`.parser` produces these, every platform consumes them. Nobody outside
the parser touches raw JSON.

Field names follow the gateway payloads (manual 5.6.2.1), including the
gateway's own spelling where it deviates from English.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from .const import (
    CONDITION_CODES,
    CONDITION_OK,
    CONDITION_UNKNOWN,
    LEVEL_MAX,
    PRIORITY_AUTOMATION,
    PRIORITY_SOURCES,
    PRIORITY_UNKNOWN,
    TC_MAX,
    TC_MIN,
)

#: The two most significant bits of ``priority`` carry the node type, the six
#: low bits the priority itself (manual 5.10.2).
PRIORITY_MASK: Final = 0b0011_1111


@dataclass(frozen=True, slots=True)
class UnitValues:
    """Parsed ``poll_device/<id>/values`` message."""

    level: int
    last_level: int
    cct_level: int
    scene: int
    vertical: int
    last_change: int

    @property
    def is_on(self) -> bool:
        """Whether the luminaire is on."""
        return self.level > 0

    @property
    def brightness(self) -> int | None:
        """Home Assistant brightness, or None while off."""
        return self.level if self.level > 0 else None


@dataclass(frozen=True, slots=True)
class UnitProperties:
    """Parsed ``poll_device/<id>/propertys`` message."""

    online: bool
    node_type: int
    priority_raw: int
    condition_raw: int
    ambient_temperature: int
    battery_level: int
    overheating: int
    general_failure: int
    last_change: int

    @property
    def condition(self) -> str:
        """Condition byte translated into a machine readable state."""
        return CONDITION_CODES.get(self.condition_raw, CONDITION_UNKNOWN)

    @property
    def has_problem(self) -> bool:
        """Whether the unit reports anything other than a healthy state."""
        return self.condition != CONDITION_OK

    @property
    def priority_source(self) -> str:
        """What last set this luminaire, translated from ``priority``.

        Values 4 to 14 are automation priorities; the named ones (presence,
        date timer, clock timer) win over the generic label.
        """
        value = self.priority_raw & PRIORITY_MASK
        if (known := PRIORITY_SOURCES.get(value)) is not None:
            return known
        if 4 <= value <= 14:
            return PRIORITY_AUTOMATION
        return PRIORITY_UNKNOWN


@dataclass(frozen=True, slots=True)
class AggregateValues:
    """Parsed ``poll_group/<n>``, ``poll_broadcast`` or ``poll_ungrouped``.

    ``level`` is an average across the member luminaires, so it says "something
    is on" rather than giving one luminaire's brightness.
    """

    level: int
    last_level: int
    cct_level: int
    vertical: int
    last_change: int

    @property
    def is_on(self) -> bool:
        """Whether at least one member is on."""
        return self.level > 0


@dataclass(frozen=True, slots=True)
class SceneValues:
    """Parsed ``poll_scene/<n>`` message."""

    active: bool
    level: int
    last_change: int

    @property
    def is_on(self) -> bool:
        """Whether the scene is currently active."""
        return self.active and self.level > 0


def kelvin_to_tc(kelvin: int, min_kelvin: int, max_kelvin: int) -> int:
    """Convert a colour temperature to the gateway's normalised 0-255 scale.

    The manual also documents a Kelvin form, but the luminaires in the
    reference installation only react to the normalised one.
    """
    if max_kelvin <= min_kelvin:
        raise ValueError("max_kelvin must be above min_kelvin")
    span = max_kelvin - min_kelvin
    scaled = round((kelvin - min_kelvin) / span * TC_MAX)
    return max(TC_MIN, min(TC_MAX, scaled))


def tc_to_kelvin(tc: int, min_kelvin: int, max_kelvin: int) -> int:
    """Inverse of :func:`kelvin_to_tc`, clamped to the configured limits."""
    if max_kelvin <= min_kelvin:
        raise ValueError("max_kelvin must be above min_kelvin")
    span = max_kelvin - min_kelvin
    value = round(min_kelvin + (tc / TC_MAX) * span)
    return max(min_kelvin, min(max_kelvin, value))


def clamp_level(value: int) -> int:
    """Clamp any brightness to the range the gateway accepts."""
    return max(0, min(LEVEL_MAX, int(value)))
