"""Package E: the light platform for the four dimmable kinds.

The fake gateway and the ``setup_units`` fixture come from ``test_entity.py``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from test_entity import (
    FakeGateway,
    group_values_from,
    make_setup_units,
    unit_values_from,
)

from custom_components.casambi_lithernet.const import DOMAIN, TargetType, UnitKind
from custom_components.casambi_lithernet.models import UnitDefinition


@pytest.fixture
def setup_units(
    hass: HomeAssistant, mqtt_mock, make_entry: Callable[..., MockConfigEntry]
) -> Callable[..., Any]:
    """Set up a bridge with the given elements and a fake gateway."""
    return make_setup_units(hass, make_entry)


SIMPLE = UnitDefinition(kind=UnitKind.SIMPLE, name="Kochnische", target_id=12)
TUNABLE = UnitDefinition(
    kind=UnitKind.TUNABLE_WHITE,
    name="Badspot 1",
    target_id=20,
    min_kelvin=2700,
    max_kelvin=6500,
)
GROUP = UnitDefinition(kind=UnitKind.GROUP, name="Kueche indirekt", target_id=2)
WOHNZIMMER = UnitDefinition(
    kind=UnitKind.MULTI_DALI,
    name="Wohnzimmer",
    target_id=15,
    dimmer_count=3,
    dimmer_names=(
        "Wohnzimmer Linear direkt",
        "Wohnzimmer indirekt 1",
        "Wohnzimmer indirekt 2",
    ),
)
GANG = UnitDefinition(
    kind=UnitKind.MULTI_DALI,
    name="Gang",
    target_id=16,
    dimmer_count=4,
    dimmer_names=("Gang Spot 1", "Gang Spot 2", "Vorraum", "Spiegellicht"),
)


# ------------------------------------------------------------- simple unit --


async def test_simple_sends_one_command_per_action(
    hass: HomeAssistant, setup_units
) -> None:
    """On, off and dimming are one ``target_level`` command each."""
    _, gateway = await setup_units([SIMPLE])

    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": "light.kochnische", "brightness": 128},
        blocking=True,
    )
    assert len(gateway.commands) == 1
    command = gateway.commands[0]
    assert (command.kind, command.target_type, command.target_id, command.value) == (
        "level",
        TargetType.UNIT,
        12,
        128,
    )

    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": "light.kochnische", "brightness": 30},
        blocking=True,
    )
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": "light.kochnische"}, blocking=True
    )
    assert gateway.levels == [128, 30, 0]


async def test_simple_reads_recorded_values(
    hass: HomeAssistant, setup_units, payload
) -> None:
    """A recorded ``values`` message becomes the entity state."""
    _, gateway = await setup_units([SIMPLE])
    assert hass.states.get("light.kochnische").state == "unknown"

    gateway.push_unit_values(12, unit_values_from(payload("unit_values_on")))
    await hass.async_block_till_done()
    state = hass.states.get("light.kochnische")
    assert state.state == "on"
    assert state.attributes["brightness"] == 4

    gateway.push_unit_values(12, unit_values_from(payload("unit_values_off")))
    await hass.async_block_till_done()
    assert hass.states.get("light.kochnische").state == "off"


async def test_retained_values_are_there_immediately(
    hass: HomeAssistant, setup_units, payload
) -> None:
    """A retained message on the broker renders the entity right away."""

    def _prepare(gateway: FakeGateway) -> None:
        gateway.seed_unit_values(12, unit_values_from(payload("unit_values_full")))

    await setup_units([SIMPLE], prepare=_prepare)
    state = hass.states.get("light.kochnische")
    assert state.state == "on"
    assert state.attributes["brightness"] == 255


# ------------------------------------------------------------ tunable white --


async def test_tunable_white_exposes_its_kelvin_limits(
    hass: HomeAssistant, setup_units
) -> None:
    """The configured limits reach Home Assistant."""
    await setup_units([TUNABLE])
    state = hass.states.get("light.badspot_1")
    assert state.attributes["min_color_temp_kelvin"] == 2700
    assert state.attributes["max_color_temp_kelvin"] == 6500
    assert state.attributes["supported_color_modes"] == ["color_temp"]


async def test_tunable_white_sends_colour_temperature_before_brightness(
    hass: HomeAssistant, setup_units
) -> None:
    """Both in one call means Tc first, then the level, and nothing else."""
    _, gateway = await setup_units([TUNABLE])
    await hass.services.async_call(
        "light",
        "turn_on",
        {
            "entity_id": "light.badspot_1",
            "brightness": 200,
            "color_temp_kelvin": 4600,
        },
        blocking=True,
    )
    assert [(c.kind, c.value) for c in gateway.commands] == [
        ("tc", 128),
        ("level", 200),
    ]
    assert gateway.commands[0].target_type is TargetType.UNIT
    assert gateway.commands[0].target_id == 20


@pytest.mark.parametrize(("kelvin", "tc"), [(2700, 0), (6500, 255)])
async def test_tunable_white_uses_the_normalised_scale(
    hass: HomeAssistant, setup_units, kelvin, tc
) -> None:
    """The gateway only understands the normalised 0-255 form."""
    _, gateway = await setup_units([TUNABLE])
    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": "light.badspot_1", "color_temp_kelvin": kelvin},
        blocking=True,
    )
    assert gateway.commands[0].kind == "tc"
    assert gateway.commands[0].value == tc


async def test_colour_temperature_only_leaves_brightness_alone(
    hass: HomeAssistant, setup_units, payload
) -> None:
    """Changing Tc on a light that is on sends no level command."""
    _, gateway = await setup_units([TUNABLE])
    gateway.push_unit_values(20, unit_values_from(payload("unit_values_full")))
    await hass.async_block_till_done()

    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": "light.badspot_1", "color_temp_kelvin": 4600},
        blocking=True,
    )
    assert [c.kind for c in gateway.commands] == ["tc"]
    assert hass.states.get("light.badspot_1").attributes["brightness"] == 255


async def test_colour_temperature_survives_a_values_message(
    hass: HomeAssistant, setup_units, payload
) -> None:
    """The gateway never reports Tc, so the entity keeps what it sent."""
    _, gateway = await setup_units([TUNABLE])
    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": "light.badspot_1", "brightness": 100, "color_temp_kelvin": 4600},
        blocking=True,
    )
    gateway.push_unit_values(20, unit_values_from(payload("unit_values_on")))
    await hass.async_block_till_done()

    state = hass.states.get("light.badspot_1")
    assert state.attributes["brightness"] == 4
    assert state.attributes["color_temp_kelvin"] == 4600


# -------------------------------------------------------------------- group --


async def test_group_is_addressed_as_a_group(hass: HomeAssistant, setup_units) -> None:
    """A group uses target type 2 and the group id."""
    _, gateway = await setup_units([GROUP])
    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": "light.kueche_indirekt", "brightness": 64},
        blocking=True,
    )
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": "light.kueche_indirekt"}, blocking=True
    )
    assert [(c.target_type, c.target_id, c.value) for c in gateway.commands] == [
        (TargetType.GROUP, 2, 64),
        (TargetType.GROUP, 2, 0),
    ]


async def test_group_reads_recorded_group_values(
    hass: HomeAssistant, setup_units, payload
) -> None:
    """A recorded ``poll_group`` message becomes the entity state."""
    _, gateway = await setup_units([GROUP])
    gateway.push_group_values(2, group_values_from(payload("group_values_on")))
    await hass.async_block_till_done()
    state = hass.states.get("light.kueche_indirekt")
    assert state.state == "on"
    assert state.attributes["brightness"] == 128

    gateway.push_group_values(2, group_values_from(payload("group_values")))
    await hass.async_block_till_done()
    assert hass.states.get("light.kueche_indirekt").state == "off"


# --------------------------------------------------------------- multi dali --


async def test_four_drivers_give_four_entities_plus_the_total(
    hass: HomeAssistant, setup_units
) -> None:
    """Unit 16 of the reference installation has four drivers."""
    await setup_units([GANG])
    entities = er.async_get(hass)
    for index, expected_name in enumerate(GANG.dimmer_names):
        entity_id = entities.async_get_entity_id(
            "light", DOMAIN, f"casambi_lithernet_0_u16_d{index}"
        )
        assert entity_id is not None
        assert hass.states.get(entity_id).attributes["friendly_name"] == expected_name

    total = entities.async_get_entity_id("light", DOMAIN, "casambi_lithernet_0_u16")
    assert total is not None

    unit_lights = [
        entity_id
        for entity_id in hass.states.async_entity_ids("light")
        if entities.async_get(entity_id).device_id
        == entities.async_get(total).device_id
    ]
    assert len(unit_lights) == 5


async def test_three_drivers_still_work(hass: HomeAssistant, setup_units) -> None:
    """Unit 15 has three drivers and one shared device."""
    await setup_units([WOHNZIMMER])
    entities = er.async_get(hass)
    device_ids = {
        entities.async_get(entity_id).device_id
        for entity_id in hass.states.async_entity_ids("light")
    }
    assert len(device_ids) == 1
    assert (
        entities.async_get_entity_id("light", DOMAIN, "casambi_lithernet_0_u15_d2")
        is not None
    )


async def test_driver_entities_are_optimistic(hass: HomeAssistant, setup_units) -> None:
    """A single driver can never be confirmed, so it assumes its state."""
    _, gateway = await setup_units([WOHNZIMMER])
    entity_id = er.async_get(hass).async_get_entity_id(
        "light", DOMAIN, "casambi_lithernet_0_u15_d1"
    )
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": entity_id, "brightness": 64}, blocking=True
    )
    state = hass.states.get(entity_id)
    assert state.state == "on"
    assert state.attributes["assumed_state"] is True
    assert state.attributes["brightness"] == 64

    command = gateway.commands[0]
    assert (command.kind, command.target_id, command.index, command.value) == (
        "dimmer",
        15,
        1,
        64,
    )


async def test_driver_state_is_not_taken_from_the_mixed_value(
    hass: HomeAssistant, setup_units, payload
) -> None:
    """The unit's mixed level says nothing about one driver."""
    _, gateway = await setup_units([WOHNZIMMER])
    entity_id = er.async_get(hass).async_get_entity_id(
        "light", DOMAIN, "casambi_lithernet_0_u15_d0"
    )
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": entity_id}, blocking=True
    )
    gateway.push_unit_values(15, unit_values_from(payload("unit_values_full")))
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "off"


async def test_total_entity_uses_the_whole_unit(
    hass: HomeAssistant, setup_units, payload
) -> None:
    """The total entity sends ``target_level`` and may show the mixed value."""
    _, gateway = await setup_units([WOHNZIMMER])
    entity_id = er.async_get(hass).async_get_entity_id(
        "light", DOMAIN, "casambi_lithernet_0_u15"
    )
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": entity_id, "brightness": 90}, blocking=True
    )
    command = gateway.commands[0]
    assert (command.kind, command.target_type, command.target_id, command.value) == (
        "level",
        TargetType.UNIT,
        15,
        90,
    )

    gateway.push_unit_values(15, unit_values_from(payload("unit_values_on")))
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).attributes["brightness"] == 4


async def test_total_entity_can_be_switched_off(
    hass: HomeAssistant, setup_units
) -> None:
    """The total entity is optional and left out when not wanted."""
    definition = UnitDefinition(
        kind=UnitKind.MULTI_DALI,
        name="Gang",
        target_id=16,
        dimmer_count=4,
        with_total_entity=False,
    )
    await setup_units([definition])
    assert len(hass.states.async_entity_ids("light")) == 4
    entities = er.async_get(hass)
    assert (
        entities.async_get_entity_id("light", DOMAIN, "casambi_lithernet_0_u16") is None
    )


async def test_driver_names_fall_back_to_numbers(
    hass: HomeAssistant, setup_units
) -> None:
    """Without names the drivers are numbered from one."""
    definition = UnitDefinition(
        kind=UnitKind.MULTI_DALI, name="Gang", target_id=16, dimmer_count=4
    )
    await setup_units([definition])
    entity_id = er.async_get(hass).async_get_entity_id(
        "light", DOMAIN, "casambi_lithernet_0_u16_d3"
    )
    assert hass.states.get(entity_id).attributes["friendly_name"] == "Gang Dimmer 4"


# ---------------------------------------------------- the one command rule --


@pytest.mark.parametrize(
    ("definition", "entity_id"),
    [
        (SIMPLE, "light.kochnische"),
        (TUNABLE, "light.badspot_1"),
        (GROUP, "light.kueche_indirekt"),
    ],
)
async def test_switching_on_with_brightness_sends_exactly_one_command(
    hass: HomeAssistant, setup_units, definition, entity_id
) -> None:
    """No extra "on" command may overwrite the brightness (document 2.5)."""
    _, gateway = await setup_units([definition])
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": entity_id, "brightness": 77}, blocking=True
    )
    assert len(gateway.commands) == 1
    assert gateway.commands[0].value == 77


async def test_switching_a_driver_on_with_brightness_sends_one_command(
    hass: HomeAssistant, setup_units
) -> None:
    """The same rule holds for a single DALI driver."""
    _, gateway = await setup_units([WOHNZIMMER])
    entity_id = er.async_get(hass).async_get_entity_id(
        "light", DOMAIN, "casambi_lithernet_0_u15_d0"
    )
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": entity_id, "brightness": 77}, blocking=True
    )
    assert len(gateway.commands) == 1
    assert gateway.commands[0].value == 77


# ------------------------------------------------------------- unique ids --


async def test_unique_ids_do_not_follow_the_name(
    hass: HomeAssistant, setup_units
) -> None:
    """Renaming an element keeps every unique id it produced."""
    renamed = UnitDefinition(
        kind=UnitKind.MULTI_DALI,
        name="Gang neu",
        target_id=16,
        dimmer_count=4,
        dimmer_names=("A", "B", "C", "D"),
    )
    await setup_units([renamed])
    entities = er.async_get(hass)
    assert (
        entities.async_get_entity_id("light", DOMAIN, "casambi_lithernet_0_u16")
        is not None
    )
    for index in range(4):
        assert (
            entities.async_get_entity_id(
                "light", DOMAIN, f"casambi_lithernet_0_u16_d{index}"
            )
            is not None
        )
