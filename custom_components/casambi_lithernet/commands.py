"""Topic and payload building.

Owned by package B. Everything in here is a pure function: parameters in,
topic and payload out. Nothing touches MQTT, Home Assistant or the clock, so
the whole command surface can be tested without a broker.

The one rule that matters most (project document 2.5): **there is no separate
"on" command**. Switching a luminaire on is a ``target_level`` with a level
above zero, and that is the only message sent. A second message with level 255
would overwrite any brightness or colour temperature sent alongside it, which
is exactly the bug this module is written to make impossible.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .const import (
    CMD_BROADCAST_LEVEL,
    CMD_SCENE_LEVEL,
    CMD_TARGET_DIMMERS,
    CMD_TARGET_LEVEL,
    CMD_TARGET_TC,
    GET_POLL_BROADCAST,
    GET_POLL_DEVICE,
    GET_POLL_GROUP,
    GET_POLL_SCENE,
    GET_POLL_UNGROUPED,
    KEY_DIMMER_INDEX,
    KEY_DIMMER_VALUE,
    KEY_DURATION,
    KEY_LEVEL,
    KEY_SCENE,
    KEY_TARGET_ID,
    KEY_TARGET_TYPE,
    KEY_TC,
    SUB_PROPERTYS,
    SUB_VALUES,
    TC_MAX,
    TC_MIN,
    TOPIC_GET,
    TOPIC_SET,
    UNGROUPED_TARGET_ID,
    TargetType,
)
from .state import clamp_level


@dataclass(frozen=True, slots=True)
class Command:
    """One message on its way to the gateway."""

    topic: str
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Topics:
    """Builds every topic of one bridge from prefix and bridge id.

    The bridge index is part of every topic (``casambi/<bridge>/...``), so a
    second gateway in the same broker never collides with the first one.
    """

    topic_prefix: str
    bridge_id: int

    @property
    def base(self) -> str:
        """Common prefix of every topic of this bridge."""
        return f"{self.topic_prefix}/{self.bridge_id}"

    # ---------------------------------------------------------- commands ---

    def command(self, name: str) -> str:
        """Topic of a command sent to the gateway."""
        return f"{self.base}/{TOPIC_SET}/{name}"

    # ------------------------------------------------------------ states ---

    def state(self, suffix: str) -> str:
        """Topic of a state message published by the gateway."""
        return f"{self.base}/{TOPIC_GET}/{suffix}"

    def unit_values(self, unit_id: int) -> str:
        """Topic carrying the values of one unit."""
        return self.state(f"{GET_POLL_DEVICE}/{unit_id}/{SUB_VALUES}")

    def unit_properties(self, unit_id: int) -> str:
        """Topic carrying the properties of one unit."""
        return self.state(f"{GET_POLL_DEVICE}/{unit_id}/{SUB_PROPERTYS}")

    def group(self, group_id: int) -> str:
        """Topic of a group; group id 0 means the ungrouped luminaires."""
        if group_id == UNGROUPED_TARGET_ID:
            return self.state(GET_POLL_UNGROUPED)
        return self.state(f"{GET_POLL_GROUP}/{group_id}")

    def scene(self, scene_id: int) -> str:
        """Topic of one scene."""
        return self.state(f"{GET_POLL_SCENE}/{scene_id}")

    def broadcast(self) -> str:
        """Topic of the whole network."""
        return self.state(GET_POLL_BROADCAST)

    def all_states(self) -> str:
        """Wildcard covering every state topic of this bridge."""
        return self.state("#")


def clamp_tc(value: int) -> int:
    """Clamp a colour temperature to the normalised range the gateway takes."""
    return max(TC_MIN, min(TC_MAX, int(value)))


def target_level(
    topics: Topics,
    target_type: TargetType,
    target_id: int,
    level: int,
    duration_ms: int,
) -> Command:
    """Build the one command that switches on, switches off and dims.

    Level 0 switches off, anything above switches on at that brightness.
    """
    return Command(
        topic=topics.command(CMD_TARGET_LEVEL),
        payload={
            KEY_LEVEL: clamp_level(level),
            KEY_DURATION: int(duration_ms),
            KEY_TARGET_ID: int(target_id),
            KEY_TARGET_TYPE: int(target_type),
        },
    )


def target_tc(
    topics: Topics,
    target_type: TargetType,
    target_id: int,
    tc: int,
    duration_ms: int,
) -> Command:
    """Build a colour temperature command on the normalised 0-255 scale.

    The manual also documents a Kelvin form; the luminaires in the reference
    installation only react to the normalised one (project document 2.3).
    """
    return Command(
        topic=topics.command(CMD_TARGET_TC),
        payload={
            KEY_TC: clamp_tc(tc),
            KEY_DURATION: int(duration_ms),
            KEY_TARGET_ID: int(target_id),
            KEY_TARGET_TYPE: int(target_type),
        },
    )


def target_dimmers(
    topics: Topics,
    target_id: int,
    dimmer_index: int,
    dimmer_value: int,
    duration_ms: int,
) -> Command:
    """Build a command for one DALI dimmer of a multi driver unit.

    Dimmer indices are zero based and always address a single unit, so the
    target type is fixed.
    """
    return Command(
        topic=topics.command(CMD_TARGET_DIMMERS),
        payload={
            KEY_DIMMER_INDEX: int(dimmer_index),
            KEY_DIMMER_VALUE: clamp_level(dimmer_value),
            KEY_DURATION: int(duration_ms),
            KEY_TARGET_ID: int(target_id),
            KEY_TARGET_TYPE: int(TargetType.UNIT),
        },
    )


def scene_level(
    topics: Topics,
    scene_id: int,
    level: int,
    duration_ms: int,
) -> Command:
    """Build a scene recall; level 0 switches the scene off again."""
    return Command(
        topic=topics.command(CMD_SCENE_LEVEL),
        payload={
            KEY_SCENE: int(scene_id),
            KEY_LEVEL: clamp_level(level),
            KEY_DURATION: int(duration_ms),
        },
    )


def broadcast_level(topics: Topics, level: int, duration_ms: int) -> Command:
    """Build the command that sets every luminaire of the network at once.

    One radio message instead of one per luminaire, which is what makes an
    "everything off" entity fast (project document 15.2).
    """
    return Command(
        topic=topics.command(CMD_BROADCAST_LEVEL),
        payload={
            KEY_LEVEL: clamp_level(level),
            KEY_DURATION: int(duration_ms),
        },
    )
