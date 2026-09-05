"""Package K: scenes and the whole network as light entities.

The fake gateway and the ``setup_units`` helper come from ``test_entity.py``.
It has no push helpers for scenes and broadcast yet, so the two small ones
below drive its subscriber lists directly.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from test_entity import FakeGateway, group_values_from, make_setup_units

from custom_components.casambi_lithernet.const import DOMAIN, UnitKind
from custom_components.casambi_lithernet.models import UnitDefinition
from custom_components.casambi_lithernet.state import AggregateValues, SceneValues


@pytest.fixture
def setup_units(
    hass: HomeAssistant, mqtt_mock, make_entry: Callable[..., MockConfigEntry]
) -> Callable[..., Any]:
    """Set up a bridge with the given elements and a fake gateway."""
    return make_setup_units(hass, make_entry)


SCENE = UnitDefinition(kind=UnitKind.SCENE, name="Abendlicht", target_id=3)
BROADCAST = UnitDefinition(kind=UnitKind.BROADCAST, name="Alle Leuchten")


def scene_values_from(raw: str) -> SceneValues:
    """Build a scene state object from a recorded ``poll_scene`` payload."""
    data = json.loads(raw)
    return SceneValues(
        active=bool(data["active"]),
        level=data["level"],
        last_change=data["last_change"],
    )


def push_scene(gateway: FakeGateway, scene_id: int, values: SceneValues) -> None:
    """Deliver a scene message to every subscriber of the fake gateway."""
    gateway._scene_values[scene_id] = values
    for callback in list(gateway._scene_subs.get(scene_id, ())):
        callback(values)


def push_broadcast(gateway: FakeGateway, values: AggregateValues) -> None:
    """Deliver a broadcast message to every subscriber of the fake gateway."""
    gateway._broadcast = values
    for callback in list(gateway._broadcast_subs):
        callback(values)


# ------------------------------------------------------------------ scene --


async def test_scene_turn_on_sends_one_call_with_the_scene_id(
    hass: HomeAssistant, setup_units
) -> None:
    """Switching a scene on is a single ``scene_level`` command."""
    _, gateway = await setup_units([SCENE])
    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": "light.abendlicht", "brightness": 180},
        blocking=True,
    )
    assert len(gateway.commands) == 1
    command = gateway.commands[0]
    assert command.kind == "scene"
    assert command.target_id == 3
    assert command.value == 180


async def test_scene_turn_off_sends_level_zero(
    hass: HomeAssistant, setup_units
) -> None:
    """Switching a scene off recalls it with level 0."""
    _, gateway = await setup_units([SCENE])
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": "light.abendlicht"}, blocking=True
    )
    assert [(c.kind, c.target_id, c.value) for c in gateway.commands] == [
        ("scene", 3, 0)
    ]


async def test_scene_transition_becomes_a_duration(
    hass: HomeAssistant, setup_units
) -> None:
    """``transition`` reaches the scene command as milliseconds."""
    _, gateway = await setup_units([SCENE])
    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": "light.abendlicht", "brightness": 10, "transition": 1.5},
        blocking=True,
    )
    assert gateway.commands[0].duration_ms == 1500


async def test_active_scene_fixture_is_on(
    hass: HomeAssistant, setup_units, payload
) -> None:
    """An active scene with a level shows as on with that brightness."""
    _, gateway = await setup_units([SCENE])
    push_scene(gateway, 3, scene_values_from(payload("scene_values_active")))
    await hass.async_block_till_done()

    state = hass.states.get("light.abendlicht")
    assert state.state == "on"
    assert state.attributes["brightness"] == 200


async def test_inactive_scene_fixture_is_off_despite_level_255(
    hass: HomeAssistant, setup_units, payload
) -> None:
    """``active: 0`` wins even though the gateway still reports level 255."""
    _, gateway = await setup_units([SCENE])
    values = scene_values_from(payload("scene_values"))
    assert values.level == 255

    push_scene(gateway, 3, values)
    await hass.async_block_till_done()

    state = hass.states.get("light.abendlicht")
    assert state.state == "off"
    assert state.attributes.get("brightness") is None


async def test_retained_scene_state_is_read_at_startup(
    hass: HomeAssistant, setup_units, payload
) -> None:
    """A scene state already on the broker renders without a new message."""

    def _prepare(gateway: FakeGateway) -> None:
        gateway._scene_values[3] = scene_values_from(payload("scene_values_active"))

    await setup_units([SCENE], prepare=_prepare)
    assert hass.states.get("light.abendlicht").state == "on"


async def test_scene_is_not_assumed(hass: HomeAssistant, setup_units) -> None:
    """The gateway polls scenes, so their state is real."""
    await setup_units([SCENE])
    attributes = hass.states.get("light.abendlicht").attributes
    assert attributes.get("assumed_state", False) is False


async def test_scene_does_not_create_a_unit_light(
    hass: HomeAssistant, setup_units
) -> None:
    """A scene definition produces exactly one entity, and not a unit one."""
    _, gateway = await setup_units([SCENE])
    entities = er.async_get(hass)
    ours = [
        entry
        for entry in entities.entities.values()
        if entry.platform == DOMAIN and entry.domain == "light"
    ]
    assert [entry.unique_id for entry in ours] == ["casambi_lithernet_0_s3"]
    assert gateway._unit_subs == {}
    assert gateway._property_subs == {}


# -------------------------------------------------------------- broadcast --


async def test_broadcast_sets_every_luminaire_with_one_command(
    hass: HomeAssistant, setup_units
) -> None:
    """Switching the network on is a single broadcast command."""
    _, gateway = await setup_units([BROADCAST])
    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": "light.alle_leuchten", "brightness": 128},
        blocking=True,
    )
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": "light.alle_leuchten"}, blocking=True
    )
    assert [(c.kind, c.value) for c in gateway.commands] == [
        ("broadcast", 128),
        ("broadcast", 0),
    ]


async def test_broadcast_fixture_shows_the_average_as_brightness(
    hass: HomeAssistant, setup_units, payload
) -> None:
    """The average level from ``poll_broadcast`` becomes the brightness."""
    _, gateway = await setup_units([BROADCAST])
    push_broadcast(gateway, group_values_from(payload("broadcast_values")))
    await hass.async_block_till_done()

    state = hass.states.get("light.alle_leuchten")
    assert state.state == "on"
    assert state.attributes["brightness"] == 31


async def test_retained_broadcast_state_is_read_at_startup(
    hass: HomeAssistant, setup_units, payload
) -> None:
    """A broadcast state already on the broker renders right away."""

    def _prepare(gateway: FakeGateway) -> None:
        gateway._broadcast = group_values_from(payload("broadcast_values"))

    await setup_units([BROADCAST], prepare=_prepare)
    assert hass.states.get("light.alle_leuchten").attributes["brightness"] == 31


async def test_broadcast_is_assumed_because_the_level_is_an_average(
    hass: HomeAssistant, setup_units
) -> None:
    """The reported level is a network average, never one luminaire's state."""
    await setup_units([BROADCAST])
    assert hass.states.get("light.alle_leuchten").attributes["assumed_state"] is True


# ------------------------------------------------------- devices and ids ---


@pytest.mark.parametrize(
    ("definition", "unique_id", "entity_id"),
    [
        (SCENE, "casambi_lithernet_0_s3", "light.abendlicht"),
        (BROADCAST, "casambi_lithernet_0_broadcast", "light.alle_leuchten"),
    ],
)
async def test_own_device_below_the_gateway(
    hass: HomeAssistant, setup_units, definition, unique_id, entity_id
) -> None:
    """Scene and broadcast each get their own device under the gateway."""
    await setup_units([definition])
    devices = dr.async_get(hass)
    device = devices.async_get_device(identifiers={(DOMAIN, unique_id)})
    assert device is not None
    assert device.name == definition.name

    gateway_device = devices.async_get_device(
        identifiers={(DOMAIN, "casambi_lithernet_0")}
    )
    assert gateway_device is not None
    assert device.via_device_id == gateway_device.id

    entities = er.async_get(hass)
    assert entities.async_get_entity_id("light", DOMAIN, unique_id) == entity_id


@pytest.mark.parametrize(
    ("definition", "renamed", "unique_id"),
    [
        (
            SCENE,
            UnitDefinition(kind=UnitKind.SCENE, name="Fernsehen", target_id=3),
            "casambi_lithernet_0_s3",
        ),
        (
            BROADCAST,
            UnitDefinition(kind=UnitKind.BROADCAST, name="Alles"),
            "casambi_lithernet_0_broadcast",
        ),
    ],
)
async def test_unique_id_is_independent_of_the_name(
    hass: HomeAssistant, setup_units, definition, renamed, unique_id
) -> None:
    """Renaming an element keeps its unique id, so entities survive."""
    await setup_units([definition])
    entities = er.async_get(hass)
    assert entities.async_get_entity_id("light", DOMAIN, unique_id) is not None
    assert renamed.base_unique_id(0) == definition.base_unique_id(0) == unique_id


async def test_scene_and_broadcast_do_not_collide(
    hass: HomeAssistant, setup_units
) -> None:
    """Both kinds side by side keep their own device and entity."""
    await setup_units([SCENE, BROADCAST])
    assert hass.states.get("light.abendlicht") is not None
    assert hass.states.get("light.alle_leuchten") is not None
