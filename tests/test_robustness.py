"""Package G: broker loss, read only entities and cleanup on removal.

Everything here runs against the real gateway of package B, because the point
of these tests is what happens at the MQTT boundary.
"""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.components.mqtt.const import MQTT_CONNECTION_STATE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from pytest_homeassistant_custom_component.common import MockConfigEntry
from test_entity import FakeGateway

from custom_components.casambi_lithernet import gateway as gateway_module
from custom_components.casambi_lithernet.const import DOMAIN, PollingMethod, UnitKind
from custom_components.casambi_lithernet.entity import CasambiEntity
from custom_components.casambi_lithernet.models import GatewayConfig, UnitDefinition

UNIT = UnitDefinition(kind=UnitKind.SIMPLE, name="Kochnische", target_id=12)
OTHER = UnitDefinition(kind=UnitKind.SIMPLE, name="Esstisch", target_id=19)

VALUES_TOPIC = "casambi/0/get/poll_device/12/values"
PROPERTIES_TOPIC = "casambi/0/get/poll_device/12/propertys"
OTHER_VALUES_TOPIC = "casambi/0/get/poll_device/19/values"


@pytest.fixture
def subscribed(monkeypatch) -> list[str]:
    """Record every topic the gateway subscribes to at MQTT level."""
    real = gateway_module.mqtt.async_subscribe
    topics: list[str] = []

    async def _spy(hass, topic, msg_callback, *args, **kwargs):
        topics.append(topic)
        return await real(hass, topic, msg_callback, *args, **kwargs)

    monkeypatch.setattr(gateway_module.mqtt, "async_subscribe", _spy)
    return topics


@pytest.fixture
async def bridge(hass: HomeAssistant, mqtt_mock, make_entry) -> MockConfigEntry:
    """Set up a bridge with two units and the real gateway object."""
    entry = make_entry(units=[UNIT.to_dict(), OTHER.to_dict()])
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def set_connection(hass: HomeAssistant, connected: bool) -> None:
    """Announce a broker connection change the way the MQTT client does."""
    async_dispatcher_send(hass, MQTT_CONNECTION_STATE, connected)
    await hass.async_block_till_done()


# ------------------------------------------------------- broker connection --


async def test_broker_loss_makes_every_entity_unavailable(
    hass: HomeAssistant, bridge: MockConfigEntry, feed
) -> None:
    """A gone broker takes the whole bridge out of service."""
    await feed(VALUES_TOPIC, {"level": 200, "last_level": 200})
    assert hass.states.get("light.kochnische").state == "on"

    await set_connection(hass, False)
    assert hass.states.get("light.kochnische").state == "unavailable"
    assert hass.states.get("light.esstisch").state == "unavailable"


async def test_entities_come_back_with_the_broker(
    hass: HomeAssistant, bridge: MockConfigEntry, feed
) -> None:
    """After the reconnect the entities are in service again."""
    await feed(VALUES_TOPIC, {"level": 200, "last_level": 200})
    await set_connection(hass, False)
    await set_connection(hass, True)

    state = hass.states.get("light.kochnische")
    assert state.state == "on"
    assert state.attributes["brightness"] == 200


async def test_retained_state_is_adopted_again_after_a_reconnect(
    hass: HomeAssistant, bridge: MockConfigEntry, feed
) -> None:
    """The retained messages the broker replays land in the entity again.

    This is the whole reason the integration survives a broker restart: it
    never asks the gateway for anything, it only listens (project document 13,
    "Retained-Abhängigkeit").
    """
    await feed(VALUES_TOPIC, {"level": 200, "last_level": 200})
    await set_connection(hass, False)
    await set_connection(hass, True)

    # What the broker replays on resubscribe: a new level and an offline unit.
    await feed(VALUES_TOPIC, {"level": 30, "last_level": 200})
    assert hass.states.get("light.kochnische").attributes["brightness"] == 30

    await feed(
        PROPERTIES_TOPIC,
        {
            "online": 0,
            "node_type": 3,
            "priority": 0,
            "condition": 0,
            "ambient_temperatur": 0,
            "battery_level": 0,
            "overheating": 0,
            "general_failure": 0,
            "last_change": 5,
        },
    )
    assert hass.states.get("light.kochnische").state == "unavailable"


async def test_subscriptions_still_stand_after_a_reconnect(
    hass: HomeAssistant, subscribed: list[str], bridge: MockConfigEntry, feed
) -> None:
    """Every state topic stays subscribed exactly once across a reconnect.

    Home Assistant's MQTT integration restores its own subscriptions on the
    broker, so the gateway must not subscribe a second time; a duplicate would
    deliver every message twice.
    """
    await set_connection(hass, False)
    await set_connection(hass, True)

    assert subscribed
    assert sorted(subscribed) == sorted(set(subscribed))
    gateway = bridge.runtime_data.gateway
    topics = gateway.diagnostics().subscribed_topics
    assert VALUES_TOPIC in topics
    assert PROPERTIES_TOPIC in topics

    await feed(VALUES_TOPIC, {"level": 11, "last_level": 11})
    assert gateway.diagnostics().messages_received == 1
    assert hass.states.get("light.kochnische").attributes["brightness"] == 11


# ---------------------------------------------------------- read only flag --


def read_only_entity(polling_method: PollingMethod) -> CasambiEntity:
    """Build a bare read only entity over a gateway with the given polling."""
    gateway = FakeGateway(GatewayConfig(polling_method=polling_method))
    return CasambiEntity(gateway, UNIT, unique_id="x", read_only=True)


@pytest.mark.parametrize(
    "polling_method", [PollingMethod.PASSIVE_37_80, PollingMethod.INACTIVE]
)
def test_read_only_entity_never_assumes_state(polling_method) -> None:
    """A reading entity has no assumed state, not even with polling inactive.

    The optimistic decision is about commands: an entity that sends nothing has
    nothing to assume, it either shows what the gateway reported or nothing.
    """
    entity = read_only_entity(polling_method)
    assert entity.assumed_state is False


def test_the_optimistic_decision_itself_is_unchanged() -> None:
    """The flag only hides ``assumed_state``, it does not rewrite the decision."""
    assert read_only_entity(PollingMethod.INACTIVE)._optimistic is True
    assert read_only_entity(PollingMethod.PASSIVE_37_80)._optimistic is False


def test_a_commanding_entity_still_assumes_state() -> None:
    """Without the flag the optimistic decision reaches ``assumed_state``."""
    gateway = FakeGateway(GatewayConfig(polling_method=PollingMethod.INACTIVE))
    assert CasambiEntity(gateway, UNIT, unique_id="x").assumed_state is True


async def test_diagnostic_entities_do_not_assume_state(
    hass: HomeAssistant, mqtt_mock, make_entry
) -> None:
    """The diagnostic entities of package J stay honest with polling inactive."""
    entry = make_entry(units=[UNIT.to_dict()], polling_method=PollingMethod.INACTIVE)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entities = er.async_get(hass)
    for platform, key in (("sensor", "condition"), ("binary_sensor", "problem")):
        entity_id = entities.async_get_entity_id(
            platform, DOMAIN, f"casambi_lithernet_0_u12_{key}"
        )
        assert entity_id is not None
        state = hass.states.get(entity_id)
        assert state.attributes.get("assumed_state", False) is False

    # The light of the same unit does assume its state, so the flag is not a
    # blanket switch.
    assert hass.states.get("light.kochnische").attributes["assumed_state"] is True


# -------------------------------------------------------------- removal ----


def registry_ids(hass: HomeAssistant, unique_id_prefix: str) -> list[str]:
    """Every registered entity whose unique id starts with the prefix."""
    return [
        entry.entity_id
        for entry in er.async_get(hass).entities.values()
        if entry.platform == DOMAIN and entry.unique_id.startswith(unique_id_prefix)
    ]


async def test_removing_an_element_leaves_nothing_behind(
    hass: HomeAssistant, bridge: MockConfigEntry, feed
) -> None:
    """Device, entities and subscriptions of a removed element all go."""
    await feed(OTHER_VALUES_TOPIC, {"level": 100, "last_level": 100})
    assert registry_ids(hass, "casambi_lithernet_0_u19")

    subentry_id = next(
        subentry_id
        for subentry_id, definition in bridge.runtime_data.units.items()
        if definition.target_id == OTHER.target_id
    )
    assert hass.config_entries.async_remove_subentry(bridge, subentry_id)
    await hass.async_block_till_done()

    devices = dr.async_get(hass)
    assert (
        devices.async_get_device(identifiers={(DOMAIN, "casambi_lithernet_0_u19")})
        is None
    )
    assert registry_ids(hass, "casambi_lithernet_0_u19") == []
    assert hass.states.get("light.esstisch") is None

    topics = bridge.runtime_data.gateway.diagnostics().subscribed_topics
    assert OTHER_VALUES_TOPIC not in topics
    assert VALUES_TOPIC in topics


async def test_the_other_elements_survive_a_removal(
    hass: HomeAssistant, bridge: MockConfigEntry, feed
) -> None:
    """Removing one element does not disturb its neighbours."""
    subentry_id = next(
        subentry_id
        for subentry_id, definition in bridge.runtime_data.units.items()
        if definition.target_id == OTHER.target_id
    )
    hass.config_entries.async_remove_subentry(bridge, subentry_id)
    await hass.async_block_till_done()

    await feed(VALUES_TOPIC, {"level": 55, "last_level": 55})
    assert hass.states.get("light.kochnische").attributes["brightness"] == 55
    assert registry_ids(hass, "casambi_lithernet_0_u12")


async def test_unloading_drops_every_subscription(
    hass: HomeAssistant, bridge: MockConfigEntry
) -> None:
    """Unloading the entry leaves no subscription behind either."""
    gateway: Any = bridge.runtime_data.gateway
    assert gateway.diagnostics().subscribed_topics

    assert await hass.config_entries.async_unload(bridge.entry_id)
    await hass.async_block_till_done()
    assert gateway.diagnostics().subscribed_topics == ()
