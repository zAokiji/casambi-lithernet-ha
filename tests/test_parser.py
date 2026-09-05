"""Package B: recorded gateway payloads to state objects.

Every fixture in ``tests/fixtures`` is exercised here, including the ones that
must not parse, so a change in the gateway's format fails in this module first.
"""

from __future__ import annotations

import pytest

from custom_components.casambi_lithernet.parser import (
    parse_aggregate_values,
    parse_scene_values,
    parse_unit_properties,
    parse_unit_values,
)

ALL_PARSERS = (
    parse_unit_values,
    parse_unit_properties,
    parse_aggregate_values,
    parse_scene_values,
)


def test_unit_values_on(payload) -> None:
    """A dimmed luminaire reports its level."""
    values = parse_unit_values(payload("unit_values_on"))
    assert values is not None
    assert values.level == 4
    assert values.last_level == 4
    assert values.cct_level == 0
    assert values.scene == 0
    assert values.last_change == 86349
    assert values.is_on
    assert values.brightness == 4


def test_unit_values_full(payload) -> None:
    """Full brightness arrives unscaled."""
    values = parse_unit_values(payload("unit_values_full"))
    assert values is not None
    assert values.level == 255
    assert values.is_on


def test_unit_values_off_keeps_the_last_level(payload) -> None:
    """A luminaire that is off still remembers what it was on at."""
    values = parse_unit_values(payload("unit_values_off"))
    assert values is not None
    assert values.level == 0
    assert values.last_level == 255
    assert not values.is_on
    assert values.brightness is None


def test_unit_properties_offline(payload) -> None:
    """An offline unit is recognisable as such."""
    properties = parse_unit_properties(payload("unit_properties_offline"))
    assert properties is not None
    assert properties.online is False
    assert properties.condition == "ok"
    assert not properties.has_problem


def test_unit_properties_online(payload) -> None:
    """A healthy unit reports no problem."""
    properties = parse_unit_properties(payload("unit_properties_online"))
    assert properties is not None
    assert properties.online is True
    assert properties.node_type == 3
    assert properties.priority_source == "none"
    assert not properties.has_problem


def test_unit_properties_condition_128_is_healthy(payload) -> None:
    """Condition 0x80 means ok, not a fault."""
    properties = parse_unit_properties(payload("unit_properties_condition_128"))
    assert properties is not None
    assert properties.condition_raw == 128
    assert properties.condition == "ok"
    assert not properties.has_problem


def test_unit_properties_lamp_failure(payload) -> None:
    """A lamp failure comes through with its diagnostic values."""
    properties = parse_unit_properties(payload("unit_properties_lamp_failure"))
    assert properties is not None
    assert properties.condition == "lamp_failure"
    assert properties.has_problem
    assert properties.general_failure == 1
    assert properties.ambient_temperature == 24
    assert properties.priority_source == "presence"


def test_unit_properties_manual_control(payload) -> None:
    """Priority 3 says a person set the luminaire by hand."""
    properties = parse_unit_properties(payload("unit_properties_manual"))
    assert properties is not None
    assert properties.priority_source == "manual_control"


def test_group_values_off(payload) -> None:
    """A group whose members are all off averages to zero."""
    values = parse_aggregate_values(payload("group_values"))
    assert values is not None
    assert values.level == 0
    assert values.last_level == 10
    assert values.cct_level == 127
    assert values.vertical == 127
    assert values.last_change == 54832
    assert not values.is_on


def test_group_values_on(payload) -> None:
    """A group with something on reports the average level."""
    values = parse_aggregate_values(payload("group_values_on"))
    assert values is not None
    assert values.level == 128
    assert values.is_on


@pytest.mark.parametrize("name", ["broadcast_values", "ungrouped_values"])
def test_broadcast_and_ungrouped_share_the_format(payload, name) -> None:
    """Broadcast and ungrouped carry exactly the fields a group does."""
    values = parse_aggregate_values(payload(name))
    assert values is not None
    assert values.level == 31
    assert values.last_level == 179
    assert values.is_on


def test_scene_values_inactive(payload) -> None:
    """An inactive scene still reports the level it would be called at."""
    values = parse_scene_values(payload("scene_values"))
    assert values is not None
    assert values.active is False
    assert values.level == 255
    assert values.last_change == 54830
    assert not values.is_on


def test_scene_values_active(payload) -> None:
    """An active scene is on."""
    values = parse_scene_values(payload("scene_values_active"))
    assert values is not None
    assert values.active is True
    assert values.level == 200
    assert values.is_on


@pytest.mark.parametrize("name", ["node_deleted", "scene_call"])
def test_other_gateway_messages_are_not_states(payload, name) -> None:
    """``node_deleted`` and ``scene_call`` are no state message of any kind."""
    raw = payload(name)
    assert all(parse(raw) is None for parse in ALL_PARSERS)


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "not json",
        "[]",
        "17",
        '"level"',
        "{}",
        '{"scene":0}',
        '{"level":"bright"}',
        '{"level":null}',
    ],
)
def test_unusable_payloads_yield_none_instead_of_raising(raw) -> None:
    """A broken message is dropped by the caller, never thrown."""
    assert all(parse(raw) is None for parse in ALL_PARSERS)


def test_properties_need_online() -> None:
    """Without ``online`` a properties message says nothing."""
    assert parse_unit_properties('{"condition":0}') is None


def test_scene_needs_active_and_level() -> None:
    """A scene message without ``active`` is not a scene state."""
    assert parse_scene_values('{"level":255}') is None
    assert parse_scene_values('{"active":1}') is None


def test_optional_fields_default_to_zero() -> None:
    """Older firmware may omit fields; the ones that matter still parse."""
    values = parse_unit_values('{"level":42}')
    assert values is not None
    assert values.level == 42
    assert values.last_level == 0
    assert values.last_change == 0


def test_broken_optional_field_does_not_cost_the_message() -> None:
    """A single unusable optional value falls back to its default."""
    values = parse_unit_values('{"level":42,"last_level":"?"}')
    assert values is not None
    assert values.last_level == 0
