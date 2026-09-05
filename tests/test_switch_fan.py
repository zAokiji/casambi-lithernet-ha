"""Package F: the switch and fan platforms for the kind ``switch``.

The fake gateway and the ``setup_units`` helper come from ``test_entity.py``.
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
    make_setup_units,
    unit_properties_from,
    unit_values_from,
)

from custom_components.casambi_lithernet.const import (
    DOMAIN,
    SWITCH_DOMAIN_FAN,
    SWITCH_DOMAIN_SWITCH,
    TargetType,
    UnitKind,
)
from custom_components.casambi_lithernet.models import UnitDefinition


@pytest.fixture
def setup_units(
    hass: HomeAssistant, mqtt_mock, make_entry: Callable[..., MockConfigEntry]
) -> Callable[..., Any]:
    """Set up a bridge with the given elements and a fake gateway."""
    return make_setup_units(hass, make_entry)


#: The WC fan of the reference installation, unit 4, today ``switch.wc_lufter``.
WC_SWITCH = UnitDefinition(
    kind=UnitKind.SWITCH,
    name="WC Luefter",
    target_id=4,
    switch_domain=SWITCH_DOMAIN_SWITCH,
)
WC_FAN = UnitDefinition(
    kind=UnitKind.SWITCH,
    name="WC Luefter",
    target_id=4,
    switch_domain=SWITCH_DOMAIN_FAN,
)

SWITCH_ENTITY = "switch.wc_luefter"
FAN_ENTITY = "fan.wc_luefter"


# ------------------------------------------------------------ which domain --


async def test_switch_domain_builds_only_a_switch(
    hass: HomeAssistant, setup_units
) -> None:
    """``switch_domain: switch`` gives one switch entity and no fan."""
    await setup_units([WC_SWITCH])
    assert hass.states.get(SWITCH_ENTITY) is not None
    assert hass.states.get(FAN_ENTITY) is None
    assert [
        state.entity_id
        for state in hass.states.async_all()
        if state.entity_id.startswith(("switch.", "fan."))
    ] == [SWITCH_ENTITY]


async def test_fan_domain_builds_only_a_fan(hass: HomeAssistant, setup_units) -> None:
    """``switch_domain: fan`` gives one fan entity and no switch."""
    await setup_units([WC_FAN])
    assert hass.states.get(FAN_ENTITY) is not None
    assert hass.states.get(SWITCH_ENTITY) is None
    assert [
        state.entity_id
        for state in hass.states.async_all()
        if state.entity_id.startswith(("switch.", "fan."))
    ] == [FAN_ENTITY]


async def test_other_kinds_build_nothing_here(hass: HomeAssistant, setup_units) -> None:
    """A dimmable luminaire is a light, never a switch or a fan."""
    simple = UnitDefinition(kind=UnitKind.SIMPLE, name="Kochnische", target_id=12)
    await setup_units([simple])
    assert [
        state.entity_id
        for state in hass.states.async_all()
        if state.entity_id.startswith(("switch.", "fan."))
    ] == []


# ------------------------------------------------------------------ unique --


async def test_unique_id_is_the_same_in_both_domains(
    hass: HomeAssistant, setup_units
) -> None:
    """The unique id comes from the address, not from the name or domain."""
    entities = er.async_get(hass)
    await setup_units([WC_SWITCH])
    assert (
        entities.async_get_entity_id("switch", DOMAIN, "casambi_lithernet_0_u4")
        == SWITCH_ENTITY
    )

    renamed = UnitDefinition(
        kind=UnitKind.SWITCH,
        name="WC Ventilator",
        target_id=4,
        switch_domain=SWITCH_DOMAIN_FAN,
    )
    assert renamed.base_unique_id(0) == WC_SWITCH.base_unique_id(0)


async def test_domain_change_moves_the_entity(hass: HomeAssistant, setup_units) -> None:
    """Switching the domain over rebuilds the element in the other domain.

    Both entities carry the same unique id, so the registry holds one entry per
    domain and only the one the definition asks for exists at a time.
    """
    entities = er.async_get(hass)
    entry, _ = await setup_units([WC_SWITCH])
    assert hass.states.get(SWITCH_ENTITY) is not None

    subentry_id = next(iter(entry.subentries))
    hass.config_entries.async_update_subentry(
        entry, entry.subentries[subentry_id], data=WC_FAN.to_dict()
    )
    await hass.async_block_till_done()

    assert hass.states.get(SWITCH_ENTITY) is None
    assert hass.states.get(FAN_ENTITY) is not None
    assert (
        entities.async_get_entity_id("fan", DOMAIN, "casambi_lithernet_0_u4")
        == FAN_ENTITY
    )


# ---------------------------------------------------------------- commands --


@pytest.mark.parametrize(
    ("definition", "domain", "entity_id"),
    [(WC_SWITCH, "switch", SWITCH_ENTITY), (WC_FAN, "fan", FAN_ENTITY)],
)
async def test_on_and_off_send_one_level_command_each(
    hass: HomeAssistant, setup_units, definition, domain, entity_id
) -> None:
    """On is level 255, off is level 0, one ``target_level`` command each."""
    _, gateway = await setup_units([definition])

    await hass.services.async_call(
        domain, "turn_on", {"entity_id": entity_id}, blocking=True
    )
    assert len(gateway.commands) == 1
    command = gateway.commands[0]
    assert (command.kind, command.target_type, command.target_id, command.value) == (
        "level",
        TargetType.UNIT,
        4,
        255,
    )

    await hass.services.async_call(
        domain, "turn_off", {"entity_id": entity_id}, blocking=True
    )
    assert len(gateway.commands) == 2
    assert gateway.commands[1].value == 0
    assert gateway.levels == [255, 0]


async def test_a_speed_on_the_fan_still_switches_fully_on(
    hass: HomeAssistant, setup_units
) -> None:
    """The fan has no steps: a percentage never reaches the gateway."""
    _, gateway = await setup_units([WC_FAN])
    await hass.services.async_call(
        "fan", "turn_on", {"entity_id": FAN_ENTITY, "percentage": 40}, blocking=True
    )
    assert gateway.levels == [255]


async def test_default_on_level_is_ignored(hass: HomeAssistant, setup_units) -> None:
    """A switching output is never dimmed, so on is always level 255."""
    dimmed_default = UnitDefinition(
        kind=UnitKind.SWITCH,
        name="WC Luefter",
        target_id=4,
        default_on_level=100,
        switch_domain=SWITCH_DOMAIN_SWITCH,
    )
    _, gateway = await setup_units([dimmed_default])
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": SWITCH_ENTITY}, blocking=True
    )
    assert gateway.levels == [255]


# ------------------------------------------------------------------- state --


@pytest.mark.parametrize(
    ("definition", "entity_id"), [(WC_SWITCH, SWITCH_ENTITY), (WC_FAN, FAN_ENTITY)]
)
async def test_reported_level_decides_on_and_off(
    hass: HomeAssistant, setup_units, payload, definition, entity_id
) -> None:
    """``level > 0`` is on, level 0 is off, in both domains."""
    _, gateway = await setup_units([definition])

    gateway.push_unit_values(4, unit_values_from(payload("unit_values_on")))
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "on"

    gateway.push_unit_values(4, unit_values_from(payload("unit_values_off")))
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "off"


async def test_retained_values_are_read_at_startup(
    hass: HomeAssistant, setup_units, payload
) -> None:
    """A retained ``values`` message renders the entity right away."""

    def _prepare(gateway: FakeGateway) -> None:
        gateway.seed_unit_values(4, unit_values_from(payload("unit_values_on")))

    await setup_units([WC_SWITCH], prepare=_prepare)
    assert hass.states.get(SWITCH_ENTITY).state == "on"


@pytest.mark.parametrize(
    ("definition", "entity_id"), [(WC_SWITCH, SWITCH_ENTITY), (WC_FAN, FAN_ENTITY)]
)
async def test_offline_unit_is_unavailable(
    hass: HomeAssistant, setup_units, payload, definition, entity_id
) -> None:
    """``online: 0`` from ``propertys`` takes the entity out of service."""
    _, gateway = await setup_units([definition])
    gateway.push_unit_properties(
        4, unit_properties_from(payload("unit_properties_offline"))
    )
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "unavailable"

    gateway.push_unit_properties(
        4, unit_properties_from(payload("unit_properties_online"))
    )
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state != "unavailable"


# --------------------------------------------------------------- fan shape --


async def test_the_fan_offers_no_speed(hass: HomeAssistant, setup_units) -> None:
    """Only on and off are supported, so no speed control is shown."""
    await setup_units([WC_FAN])
    attributes = hass.states.get(FAN_ENTITY).attributes
    assert attributes["supported_features"] == 48  # TURN_OFF | TURN_ON
    assert "percentage" not in attributes
    assert "preset_mode" not in attributes


async def test_the_fan_reports_on_without_a_percentage(
    hass: HomeAssistant, setup_units, payload
) -> None:
    """``is_on`` comes from the level, not from a speed percentage."""
    _, gateway = await setup_units([WC_FAN])
    await hass.services.async_call(
        "fan", "turn_on", {"entity_id": FAN_ENTITY}, blocking=True
    )
    gateway.push_unit_values(4, unit_values_from(payload("unit_values_on")))
    await hass.async_block_till_done()
    assert hass.states.get(FAN_ENTITY).state == "on"


# ------------------------------------------------- the real WC fan, unit 4 --


async def test_wc_fan_matches_the_existing_yaml_entity(
    hass: HomeAssistant, setup_units, payload
) -> None:
    """Unit 4 as a ``switch`` behaves like today's ``switch.wc_lufter``."""
    _, gateway = await setup_units([WC_SWITCH])
    state = hass.states.get(SWITCH_ENTITY)
    assert state is not None
    assert state.attributes["friendly_name"] == "WC Luefter"

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": SWITCH_ENTITY}, blocking=True
    )
    assert gateway.commands[0].target_id == 4
    assert gateway.levels == [255]

    gateway.push_unit_values(4, unit_values_from(payload("unit_values_on")))
    await hass.async_block_till_done()
    assert hass.states.get(SWITCH_ENTITY).state == "on"

    gateway.set_available(False)
    await hass.async_block_till_done()
    assert hass.states.get(SWITCH_ENTITY).state == "unavailable"
