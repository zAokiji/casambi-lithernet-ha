"""Package E: the shared entity base class.

The fake gateway and the payload helpers moved to ``fake_gateway.py`` (package
H) so that no test module has to import from another test module any more. They
are re-exported here unchanged, because ``test_light.py``,
``test_switch_fan.py``, ``test_diagnostics_entities.py`` and
``test_scene_broadcast.py`` import them from this module.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from fake_gateway import (
    Command,
    FakeGateway,
    group_values_from,
    make_setup_units,
    scene_values_from,
    unit_properties_from,
    unit_values_from,
)
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.casambi_lithernet.const import (
    DOMAIN,
    STATE_CONFIRM_TIMEOUT,
    PollingMethod,
    UnitKind,
)
from custom_components.casambi_lithernet.models import UnitDefinition

#: Re-exported for the test modules that still import them from here.
__all__ = [
    "Command",
    "FakeGateway",
    "group_values_from",
    "make_setup_units",
    "scene_values_from",
    "unit_properties_from",
    "unit_values_from",
]


@pytest.fixture
def setup_units(
    hass: HomeAssistant, mqtt_mock, make_entry: Callable[..., MockConfigEntry]
) -> Callable[..., Any]:
    """Set up a bridge with the given elements and a fake gateway."""
    return make_setup_units(hass, make_entry)


SIMPLE = UnitDefinition(kind=UnitKind.SIMPLE, name="Kochnische", target_id=12)
GROUP = UnitDefinition(kind=UnitKind.GROUP, name="Kueche indirekt", target_id=2)


# ------------------------------------------------------------------ tests --


async def test_device_links_to_the_gateway(hass: HomeAssistant, setup_units) -> None:
    """Every element becomes its own device below the gateway device."""
    entry, _ = await setup_units([SIMPLE])
    devices = dr.async_get(hass)
    device = devices.async_get_device(identifiers={(DOMAIN, "casambi_lithernet_0_u12")})
    assert device is not None
    assert device.name == "Kochnische"
    gateway_device = devices.async_get_device(
        identifiers={(DOMAIN, "casambi_lithernet_0")}
    )
    assert gateway_device is not None
    assert device.via_device_id == gateway_device.id
    assert entry.entry_id in device.config_entries


async def test_unique_id_survives_a_rename(hass: HomeAssistant, setup_units) -> None:
    """The unique id comes from the address, never from the name."""
    _, _ = await setup_units([SIMPLE])
    entities = er.async_get(hass)
    entity_id = entities.async_get_entity_id("light", DOMAIN, "casambi_lithernet_0_u12")
    assert entity_id is not None

    renamed = UnitDefinition(
        kind=UnitKind.SIMPLE, name="Kueche Kochnische", target_id=12
    )
    assert renamed.base_unique_id(0) == SIMPLE.base_unique_id(0)


async def test_offline_unit_is_unavailable(
    hass: HomeAssistant, setup_units, payload
) -> None:
    """``online: 0`` from ``propertys`` takes the entity out of service."""
    _, gateway = await setup_units([SIMPLE])
    gateway.push_unit_properties(
        12, unit_properties_from(payload("unit_properties_offline"))
    )
    await hass.async_block_till_done()
    assert hass.states.get("light.kochnische").state == "unavailable"

    gateway.push_unit_properties(
        12, unit_properties_from(payload("unit_properties_online"))
    )
    await hass.async_block_till_done()
    assert hass.states.get("light.kochnische").state != "unavailable"


async def test_retained_offline_properties_are_read_at_startup(
    hass: HomeAssistant, setup_units, payload
) -> None:
    """A retained ``online: 0`` is honoured before any message arrives."""

    def _prepare(gateway: FakeGateway) -> None:
        gateway.seed_unit_properties(
            12, unit_properties_from(payload("unit_properties_offline"))
        )

    await setup_units([SIMPLE], prepare=_prepare)
    assert hass.states.get("light.kochnische").state == "unavailable"


async def test_broker_loss_makes_everything_unavailable(
    hass: HomeAssistant, setup_units
) -> None:
    """A disconnected broker beats everything else."""
    _, gateway = await setup_units([SIMPLE, GROUP])
    assert hass.states.get("light.kueche_indirekt").state != "unavailable"

    gateway.set_available(False)
    await hass.async_block_till_done()
    assert hass.states.get("light.kochnische").state == "unavailable"
    assert hass.states.get("light.kueche_indirekt").state == "unavailable"

    gateway.set_available(True)
    await hass.async_block_till_done()
    assert hass.states.get("light.kueche_indirekt").state != "unavailable"


async def test_group_has_no_online_stage(hass: HomeAssistant, setup_units) -> None:
    """A group has no ``propertys``, so it only hangs on the broker."""
    _, gateway = await setup_units([GROUP])
    assert gateway._property_subs == {}
    assert hass.states.get("light.kueche_indirekt").state != "unavailable"


async def test_fallback_adopts_the_sent_value(
    hass: HomeAssistant, setup_units, freezer: FrozenDateTimeFactory
) -> None:
    """Without a confirmation the entity adopts what it sent after 3 s."""
    _, _ = await setup_units([SIMPLE])
    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": "light.kochnische", "brightness": 100},
        blocking=True,
    )
    assert hass.states.get("light.kochnische").state == "unknown"

    freezer.tick(timedelta(seconds=STATE_CONFIRM_TIMEOUT + 1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    state = hass.states.get("light.kochnische")
    assert state.state == "on"
    assert state.attributes["brightness"] == 100


async def test_confirmation_wins_over_the_fallback(
    hass: HomeAssistant, setup_units, freezer: FrozenDateTimeFactory, payload
) -> None:
    """A real state message cancels the fallback, so the gateway wins."""
    _, gateway = await setup_units([SIMPLE])
    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": "light.kochnische", "brightness": 100},
        blocking=True,
    )
    gateway.push_unit_values(12, unit_values_from(payload("unit_values_on")))
    await hass.async_block_till_done()
    assert hass.states.get("light.kochnische").attributes["brightness"] == 4

    freezer.tick(timedelta(seconds=STATE_CONFIRM_TIMEOUT + 1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("light.kochnische").attributes["brightness"] == 4


async def test_optimistic_entity_updates_at_once(
    hass: HomeAssistant, setup_units
) -> None:
    """An element forced optimistic assumes its state and says so."""
    optimistic = UnitDefinition(
        kind=UnitKind.SIMPLE, name="Vorraum", target_id=9, optimistic_override=True
    )
    await setup_units([optimistic])
    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": "light.vorraum", "brightness": 77},
        blocking=True,
    )
    state = hass.states.get("light.vorraum")
    assert state.state == "on"
    assert state.attributes["brightness"] == 77
    assert state.attributes["assumed_state"] is True


@pytest.mark.parametrize(
    ("polling_method", "optimistic"),
    [(PollingMethod.PASSIVE_37_80, False), (PollingMethod.INACTIVE, True)],
)
async def test_polling_method_decides_optimistic_mode(
    hass: HomeAssistant, setup_units, polling_method, optimistic
) -> None:
    """The same element is real with polling, optimistic without it."""
    await setup_units([SIMPLE], polling_method=polling_method)
    attributes = hass.states.get("light.kochnische").attributes
    assert attributes.get("assumed_state", False) is optimistic


async def test_unticked_override_does_not_undo_inactive_polling(
    hass: HomeAssistant, setup_units
) -> None:
    """A stored ``False`` means "do not force", not "never optimistic"."""
    unit = UnitDefinition(
        kind=UnitKind.SIMPLE, name="Kochnische", target_id=12, optimistic_override=False
    )
    await setup_units([unit], polling_method=PollingMethod.INACTIVE)
    assert hass.states.get("light.kochnische").attributes["assumed_state"] is True


async def test_last_level_is_reused_on_the_next_switch_on(
    hass: HomeAssistant, setup_units
) -> None:
    """Switching on without a brightness repeats the last one."""
    optimistic = UnitDefinition(
        kind=UnitKind.SIMPLE, name="Vorraum", target_id=9, optimistic_override=True
    )
    _, gateway = await setup_units([optimistic])
    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": "light.vorraum", "brightness": 42},
        blocking=True,
    )
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": "light.vorraum"}, blocking=True
    )
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": "light.vorraum"}, blocking=True
    )
    assert gateway.levels == [42, 0, 42]


async def test_default_on_level_applies_before_anything_was_set(
    hass: HomeAssistant, setup_units
) -> None:
    """The configured default is used until a brightness is known."""
    unit = UnitDefinition(
        kind=UnitKind.SIMPLE,
        name="Vorraum",
        target_id=9,
        default_on_level=180,
        optimistic_override=True,
    )
    _, gateway = await setup_units([unit])
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": "light.vorraum"}, blocking=True
    )
    assert gateway.levels == [180]


async def test_transition_becomes_a_duration(hass: HomeAssistant, setup_units) -> None:
    """``transition`` in seconds turns into ``duration`` in milliseconds."""
    _, gateway = await setup_units([SIMPLE])
    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": "light.kochnische", "brightness": 10, "transition": 2.5},
        blocking=True,
    )
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": "light.kochnische"}, blocking=True
    )
    assert [c.duration_ms for c in gateway.commands] == [2500, None]
