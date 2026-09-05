"""Package J: the diagnostic entities built from ``propertys``.

The fake gateway, the setup helper and the payload converters come from
``test_entity.py`` ; this module only adds what the diagnostic
entities need on top.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from test_entity import FakeGateway, make_setup_units, unit_properties_from

from custom_components.casambi_lithernet.const import DOMAIN, UnitKind
from custom_components.casambi_lithernet.models import UnitDefinition
from custom_components.casambi_lithernet.state import UnitProperties

UNIT = UnitDefinition(kind=UnitKind.SIMPLE, name="Kochnische", target_id=12)
GROUP = UnitDefinition(kind=UnitKind.GROUP, name="Kueche indirekt", target_id=2)
SCENE = UnitDefinition(kind=UnitKind.SCENE, name="Abend", target_id=4)

#: Suffix of the unique id -> platform, for the six enabled-by-default and the
#: four optional entities of one unit.
ENABLED_KEYS = {
    "problem": "binary_sensor",
    "condition": "sensor",
    "priority_source": "sensor",
}
DISABLED_KEYS = {
    "battery_level": "sensor",
    "ambient_temperature": "sensor",
    "overheating": "sensor",
    "general_failure": "sensor",
}


@pytest.fixture
def setup_units(
    hass: HomeAssistant, mqtt_mock, make_entry: Callable[..., MockConfigEntry]
) -> Callable[..., Any]:
    """Set up a bridge with the given elements and a fake gateway."""
    return make_setup_units(hass, make_entry)


def entity_id_of(hass: HomeAssistant, platform: str, key: str) -> str | None:
    """Look up an entity of unit 12 by its unique id, not by its name."""
    return er.async_get(hass).async_get_entity_id(
        platform, DOMAIN, f"casambi_lithernet_0_u{UNIT.target_id}_{key}"
    )


def properties(**overrides: Any) -> UnitProperties:
    """Build a properties object, healthy unless told otherwise."""
    fields: dict[str, Any] = {
        "online": True,
        "node_type": 3,
        "priority_raw": 0,
        "condition_raw": 0,
        "ambient_temperature": 0,
        "battery_level": 0,
        "overheating": 0,
        "general_failure": 0,
        "last_change": 1,
    }
    fields.update(overrides)
    return UnitProperties(**fields)


# --------------------------------------------------------- which entities --


async def test_unit_gets_exactly_the_expected_entities(
    hass: HomeAssistant, setup_units
) -> None:
    """One unit produces one problem sensor and six diagnostic sensors."""
    await setup_units([UNIT])
    entities = er.async_get(hass)
    entries = [
        entry
        for entry in entities.entities.values()
        if entry.domain in ("sensor", "binary_sensor")
    ]
    suffixes = {
        entry.unique_id.removeprefix("casambi_lithernet_0_u12_") for entry in entries
    }
    assert suffixes == set(ENABLED_KEYS) | set(DISABLED_KEYS)
    assert all(entry.entity_category is EntityCategory.DIAGNOSTIC for entry in entries)


async def test_group_and_scene_get_no_diagnostics(
    hass: HomeAssistant, setup_units
) -> None:
    """Elements without ``propertys`` get no diagnostic entities at all."""
    await setup_units([GROUP, SCENE])
    entities = er.async_get(hass)
    assert not [
        entry
        for entry in entities.entities.values()
        if entry.domain in ("sensor", "binary_sensor")
    ]


@pytest.mark.parametrize("key", sorted(DISABLED_KEYS))
async def test_optional_sensors_start_disabled(
    hass: HomeAssistant, setup_units, key: str
) -> None:
    """The four raw value sensors are registered but have no state."""
    await setup_units([UNIT])
    entity_id = entity_id_of(hass, DISABLED_KEYS[key], key)
    assert entity_id is not None
    entry = er.async_get(hass).async_get(entity_id)
    assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert hass.states.get(entity_id) is None


@pytest.mark.parametrize("key", sorted(ENABLED_KEYS))
async def test_default_sensors_are_enabled(
    hass: HomeAssistant, setup_units, key: str
) -> None:
    """Problem, condition and control source are there without being asked."""
    await setup_units([UNIT])
    entity_id = entity_id_of(hass, ENABLED_KEYS[key], key)
    assert entity_id is not None
    assert er.async_get(hass).async_get(entity_id).disabled_by is None
    assert hass.states.get(entity_id) is not None


async def test_diagnostics_share_the_device_with_the_light(
    hass: HomeAssistant, setup_units
) -> None:
    """Every entity of one element sits on the same Home Assistant device."""
    await setup_units([UNIT])
    entities = er.async_get(hass)
    light_id = entities.async_get_entity_id("light", DOMAIN, "casambi_lithernet_0_u12")
    device_id = entities.async_get(light_id).device_id
    assert device_id is not None
    for key, platform in (ENABLED_KEYS | DISABLED_KEYS).items():
        entity_id = entity_id_of(hass, platform, key)
        assert entities.async_get(entity_id).device_id == device_id


# ------------------------------------------------------------- the values --


@pytest.mark.parametrize(
    "fixture", ["unit_properties_online", "unit_properties_condition_128"]
)
async def test_healthy_conditions_report_ok(
    hass: HomeAssistant, setup_units, payload, fixture: str
) -> None:
    """``condition`` 0 and 128 both mean the luminaire is fine."""
    _, gateway = await setup_units([UNIT])
    gateway.push_unit_properties(12, unit_properties_from(payload(fixture)))
    await hass.async_block_till_done()

    assert hass.states.get(entity_id_of(hass, "sensor", "condition")).state == "ok"
    assert (
        hass.states.get(entity_id_of(hass, "binary_sensor", "problem")).state == "off"
    )


async def test_lamp_failure_is_a_problem(
    hass: HomeAssistant, setup_units, payload
) -> None:
    """``condition`` 130 is a lamp failure and raises the problem sensor."""
    _, gateway = await setup_units([UNIT])
    gateway.push_unit_properties(
        12, unit_properties_from(payload("unit_properties_lamp_failure"))
    )
    await hass.async_block_till_done()

    assert (
        hass.states.get(entity_id_of(hass, "sensor", "condition")).state
        == "lamp_failure"
    )
    assert hass.states.get(entity_id_of(hass, "binary_sensor", "problem")).state == "on"


async def test_manual_control_is_reported_as_the_source(
    hass: HomeAssistant, setup_units, payload
) -> None:
    """``priority`` 3 names the hand that dimmed the luminaire."""
    _, gateway = await setup_units([UNIT])
    gateway.push_unit_properties(
        12, unit_properties_from(payload("unit_properties_manual"))
    )
    await hass.async_block_till_done()
    state = hass.states.get(entity_id_of(hass, "sensor", "priority_source"))
    assert state.state == "manual_control"
    assert "manual_control" in state.attributes["options"]


async def test_presence_is_reported_as_the_source(
    hass: HomeAssistant, setup_units, payload
) -> None:
    """``priority`` 8 is the presence detector, taken from the same fixture."""
    _, gateway = await setup_units([UNIT])
    gateway.push_unit_properties(
        12, unit_properties_from(payload("unit_properties_lamp_failure"))
    )
    await hass.async_block_till_done()
    assert (
        hass.states.get(entity_id_of(hass, "sensor", "priority_source")).state
        == "presence"
    )


@pytest.mark.parametrize("priority_raw", [5, 14])
async def test_automation_priorities_share_one_state(
    hass: HomeAssistant, setup_units, priority_raw: int
) -> None:
    """The unnamed priorities 4 to 14 all read as a Casambi automation."""
    _, gateway = await setup_units([UNIT])
    gateway.push_unit_properties(12, properties(priority_raw=priority_raw))
    await hass.async_block_till_done()
    assert (
        hass.states.get(entity_id_of(hass, "sensor", "priority_source")).state
        == "automation"
    )


async def test_unknown_raw_values_do_not_break_the_entities(
    hass: HomeAssistant, setup_units
) -> None:
    """A code that is in no table reports "unrecognized", not an error.

    The state deliberately is not "unknown": Home Assistant uses that for "no
    data yet", and the two must stay distinguishable in the logbook.
    """
    _, gateway = await setup_units([UNIT])
    gateway.push_unit_properties(12, properties(condition_raw=0x42, priority_raw=0x1F))
    await hass.async_block_till_done()

    assert (
        hass.states.get(entity_id_of(hass, "sensor", "condition")).state
        == "unrecognized"
    )
    assert (
        hass.states.get(entity_id_of(hass, "sensor", "priority_source")).state
        == "unrecognized"
    )
    assert hass.states.get(entity_id_of(hass, "binary_sensor", "problem")).state == "on"


async def test_retained_properties_are_read_at_startup(
    hass: HomeAssistant, setup_units, payload
) -> None:
    """A retained message renders the sensors before anything is pushed."""

    def _prepare(gateway: FakeGateway) -> None:
        gateway.seed_unit_properties(
            12, unit_properties_from(payload("unit_properties_manual"))
        )

    await setup_units([UNIT], prepare=_prepare)
    assert (
        hass.states.get(entity_id_of(hass, "sensor", "priority_source")).state
        == "manual_control"
    )


# ------------------------------------------------------- availability ----


async def test_offline_unit_makes_the_diagnostics_unavailable(
    hass: HomeAssistant, setup_units, payload
) -> None:
    """``online: 0`` takes the diagnostic entities out of service too."""
    _, gateway = await setup_units([UNIT])
    gateway.push_unit_properties(
        12, unit_properties_from(payload("unit_properties_offline"))
    )
    await hass.async_block_till_done()
    for key, platform in ENABLED_KEYS.items():
        state = hass.states.get(entity_id_of(hass, platform, key))
        assert state.state == "unavailable"

    gateway.push_unit_properties(
        12, unit_properties_from(payload("unit_properties_online"))
    )
    await hass.async_block_till_done()
    assert hass.states.get(entity_id_of(hass, "sensor", "condition")).state == "ok"


async def test_broker_loss_makes_the_diagnostics_unavailable(
    hass: HomeAssistant, setup_units
) -> None:
    """A disconnected broker beats the properties."""
    _, gateway = await setup_units([UNIT])
    gateway.set_available(False)
    await hass.async_block_till_done()
    assert (
        hass.states.get(entity_id_of(hass, "binary_sensor", "problem")).state
        == "unavailable"
    )
