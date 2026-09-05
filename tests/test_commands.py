"""Package B: topic and payload building."""

from __future__ import annotations

import pytest

from custom_components.casambi_lithernet.commands import (
    Topics,
    broadcast_level,
    clamp_tc,
    scene_level,
    target_dimmers,
    target_level,
    target_tc,
)
from custom_components.casambi_lithernet.const import TargetType
from custom_components.casambi_lithernet.state import kelvin_to_tc

TOPICS = Topics("casambi", 0)


def test_command_topics_carry_the_bridge_id() -> None:
    """A second bridge never publishes on the first one's topics."""
    other = Topics("casambi", 3)
    assert TOPICS.command("target_level") == "casambi/0/set/target_level"
    assert other.command("target_level") == "casambi/3/set/target_level"


@pytest.mark.parametrize(
    ("builder", "expected"),
    [
        (lambda t: t.unit_values(19), "casambi/0/get/poll_device/19/values"),
        (lambda t: t.unit_properties(19), "casambi/0/get/poll_device/19/propertys"),
        (lambda t: t.group(2), "casambi/0/get/poll_group/2"),
        (lambda t: t.group(0), "casambi/0/get/poll_ungrouped"),
        (lambda t: t.scene(1), "casambi/0/get/poll_scene/1"),
        (lambda t: t.broadcast(), "casambi/0/get/poll_broadcast"),
        (lambda t: t.all_states(), "casambi/0/get/#"),
    ],
)
def test_state_topics(builder, expected) -> None:
    """Every state topic is built from prefix and bridge id alone."""
    assert builder(TOPICS) == expected


def test_topic_prefix_is_configurable() -> None:
    """Installations that renamed the prefix still work."""
    assert Topics("licht", 1).unit_values(4) == "licht/1/get/poll_device/4/values"


def test_target_level_carries_target_type() -> None:
    """A unit command addresses target type 1."""
    command = target_level(TOPICS, TargetType.UNIT, 19, 128, 0)
    assert command.topic == "casambi/0/set/target_level"
    assert command.payload == {
        "level": 128,
        "duration": 0,
        "targetid": 19,
        "targettype": 1,
    }


@pytest.mark.parametrize(
    ("target_type", "target_id", "expected_type"),
    [
        (TargetType.BROADCAST, 0, 0),
        (TargetType.UNIT, 19, 1),
        (TargetType.GROUP, 0, 2),
        (TargetType.GROUP, 2, 2),
        (TargetType.SCENE_ACTIVE, 1, 3),
        (TargetType.SCENE_ALL, 1, 4),
    ],
)
def test_every_target_type_is_serialised_as_a_number(
    target_type, target_id, expected_type
) -> None:
    """``targettype`` goes on the wire as the number from the manual."""
    payload = target_level(TOPICS, target_type, target_id, 255, 0).payload
    assert payload["targettype"] == expected_type
    assert payload["targetid"] == target_id


def test_level_zero_is_the_off_command() -> None:
    """Switching off is a level command, not a command of its own."""
    command = target_level(TOPICS, TargetType.UNIT, 19, 0, 0)
    assert command.topic == "casambi/0/set/target_level"
    assert command.payload["level"] == 0


@pytest.mark.parametrize(
    ("level", "expected"), [(-5, 0), (0, 0), (255, 255), (999, 255)]
)
def test_levels_are_clamped(level, expected) -> None:
    """Nothing outside 0-255 ever reaches the gateway."""
    assert target_level(TOPICS, TargetType.UNIT, 19, level, 0).payload["level"] == (
        expected
    )


def test_duration_is_sent_in_milliseconds() -> None:
    """The transition time is passed through unchanged."""
    payload = target_level(TOPICS, TargetType.UNIT, 19, 128, 1500).payload
    assert payload["duration"] == 1500


def test_target_tc_uses_the_normalised_scale() -> None:
    """Colour temperature is sent as 0-255, not as Kelvin."""
    command = target_tc(TOPICS, TargetType.UNIT, 16, kelvin_to_tc(4600, 2700, 6500), 0)
    assert command.topic == "casambi/0/set/target_tc"
    assert command.payload == {
        "tc": 128,
        "duration": 0,
        "targetid": 16,
        "targettype": 1,
    }


@pytest.mark.parametrize(
    ("kelvin", "expected_tc"), [(2700, 0), (6500, 255), (1000, 0), (9000, 255)]
)
def test_tc_hits_and_holds_the_edges(kelvin, expected_tc) -> None:
    """Both ends of the configured Kelvin range map onto the scale exactly."""
    tc = kelvin_to_tc(kelvin, 2700, 6500)
    assert target_tc(TOPICS, TargetType.UNIT, 16, tc, 0).payload["tc"] == expected_tc


@pytest.mark.parametrize(
    ("value", "expected"), [(-1, 0), (0, 0), (255, 255), (300, 255)]
)
def test_clamp_tc(value, expected) -> None:
    """A colour temperature outside the scale is clamped, not rejected."""
    assert clamp_tc(value) == expected


def test_target_dimmers_addresses_one_driver_of_a_unit() -> None:
    """A DALI dimmer is always addressed as a unit, index zero based."""
    command = target_dimmers(TOPICS, 15, 2, 64, 0)
    assert command.topic == "casambi/0/set/target_dimmers"
    assert command.payload == {
        "dimmer_index": 2,
        "dimmer_value": 64,
        "duration": 0,
        "targetid": 15,
        "targettype": 1,
    }


def test_scene_level() -> None:
    """A scene is recalled with its number and a level."""
    command = scene_level(TOPICS, 3, 200, 0)
    assert command.topic == "casambi/0/set/scene_level"
    assert command.payload == {"scene": 3, "level": 200, "duration": 0}


def test_scene_level_zero_switches_the_scene_off() -> None:
    """There is no separate scene off command either."""
    assert scene_level(TOPICS, 3, 0, 0).payload["level"] == 0


def test_broadcast_level_has_no_target() -> None:
    """The broadcast command addresses the whole network implicitly."""
    command = broadcast_level(TOPICS, 0, 500)
    assert command.topic == "casambi/0/set/level"
    assert command.payload == {"level": 0, "duration": 500}
