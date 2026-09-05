"""What an entity has to let go of when it is removed.

Runs against the real gateway with a mocked broker.

Two leaks are covered, both of which a test on the gateway's *topic* list would
miss, because a topic several entities share stays subscribed either way:

1. **Every subscription is registered with ``async_on_remove``.** Dropping one
   of those registrations in ``async_added_to_hass`` leaves a dead entity in
   the gateway's listener list. It keeps being handed messages, keeps writing
   state for an entity Home Assistant no longer knows, and only a restart gets
   rid of it. The fixture below therefore counts the subscriptions that are
   still live, not the topics.
2. **The three second confirmation timer is cancelled on removal.** A non
   optimistic entity that was just commanded has a pending
   :func:`~homeassistant.helpers.event.async_call_later`. If removal does not
   cancel it, it fires afterwards and writes the state of an entity that is
   gone.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from datetime import timedelta
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.casambi_lithernet.const import STATE_CONFIRM_TIMEOUT, UnitKind
from custom_components.casambi_lithernet.gateway import MqttCasambiGateway
from custom_components.casambi_lithernet.models import UnitDefinition

SIMPLE = UnitDefinition(kind=UnitKind.SIMPLE, name="Kochnische", target_id=12)

#: Every way an entity can register itself with the gateway.
SUBSCRIBE_METHODS = (
    "subscribe_availability",
    "subscribe_unit",
    "subscribe_unit_properties",
    "subscribe_group",
    "subscribe_scene",
    "subscribe_broadcast",
)


@pytest.fixture
def live_subscriptions(monkeypatch) -> list[str]:
    """List the gateway subscriptions that have not been removed again.

    Every ``subscribe_*`` of the real gateway is wrapped so that a subscription
    is noted when it is taken out and dropped again when the unsubscribe it
    returned is called. What stays in the list after an entity was removed is a
    listener that entity leaked.
    """
    live: list[str] = []

    def _wrap(name: str) -> None:
        original = getattr(MqttCasambiGateway, name)

        def _patched(
            self: MqttCasambiGateway, *args: Any, **kwargs: Any
        ) -> Callable[[], None]:
            remove = original(self, *args, **kwargs)
            # The callback is the last positional argument; what is left
            # identifies the topic the subscription is for.
            handle = f"{name}{args[:-1]}"
            live.append(handle)

            def _remove() -> None:
                if handle in live:
                    live.remove(handle)
                remove()

            return _remove

        monkeypatch.setattr(MqttCasambiGateway, name, _patched)

    for method in SUBSCRIBE_METHODS:
        _wrap(method)
    return live


async def setup_bridge(
    hass: HomeAssistant,
    make_entry: Callable[..., MockConfigEntry],
    units: Iterable[UnitDefinition],
) -> MockConfigEntry:
    """Set up one bridge with the real gateway and the given elements."""
    entry = make_entry(units=[unit.to_dict() for unit in units])
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def remove_every_entity(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Delete every entity of the config entry, as a user would."""
    registry = er.async_get(hass)
    for registry_entry in list(
        er.async_entries_for_config_entry(registry, entry.entry_id)
    ):
        registry.async_remove(registry_entry.entity_id)
    await hass.async_block_till_done()


async def test_removing_the_entities_releases_every_listener(
    hass: HomeAssistant, mqtt_mock, make_entry, live_subscriptions
) -> None:
    """An entity that goes away takes all of its subscriptions with it.

    Availability, ``propertys`` and the entity's own state topic are three
    separate subscriptions, and each of them has to be registered with
    ``async_on_remove``. The gateway's topic list cannot show a leak here: the
    diagnostic entities of the same unit keep ``propertys`` subscribed anyway.
    """
    entry = await setup_bridge(hass, make_entry, [SIMPLE])
    gateway = entry.runtime_data.gateway
    assert live_subscriptions
    # The light alone accounts for availability, propertys and values.
    assert live_subscriptions.count("subscribe_unit(12,)") == 1
    assert live_subscriptions.count("subscribe_availability()") >= 1

    await remove_every_entity(hass, entry)

    assert live_subscriptions == []
    assert gateway.diagnostics().subscribed_topics == ()


async def test_removing_one_entity_leaves_the_others_subscribed(
    hass: HomeAssistant, mqtt_mock, make_entry, live_subscriptions
) -> None:
    """Only the listeners of the removed entity go, not the shared topic.

    Two elements of the same Casambi unit (section 7 allows that) watch one
    values topic. Removing one of them has to take exactly one listener out of
    the gateway while the topic stays subscribed for the other.
    """
    entry = await setup_bridge(
        hass,
        make_entry,
        [
            SIMPLE,
            UnitDefinition(
                kind=UnitKind.SWITCH, name="Kochnische Schalter", target_id=12
            ),
        ],
    )
    gateway = entry.runtime_data.gateway
    values_topic = "casambi/0/get/poll_device/12/values"
    assert live_subscriptions.count("subscribe_unit(12,)") == 2
    assert listener_count(gateway, values_topic) == 2

    er.async_get(hass).async_remove("switch.kochnische_schalter")
    await hass.async_block_till_done()

    assert live_subscriptions.count("subscribe_unit(12,)") == 1
    assert listener_count(gateway, values_topic) == 1
    assert values_topic in gateway.diagnostics().subscribed_topics
    assert hass.states.get("light.kochnische") is not None


def listener_count(gateway: MqttCasambiGateway, topic: str) -> int:
    """How many listeners the gateway still fans a topic out to.

    Reaches into the gateway on purpose: the point of these tests is the
    receiver list behind a topic, which nothing public exposes. The topic list
    in the diagnostics only says whether *anybody* is listening.
    """
    subscription = gateway._subscriptions.get(topic)
    return 0 if subscription is None else len(subscription.listeners)


async def test_removing_an_entity_cancels_its_confirmation_timer(
    hass: HomeAssistant,
    mqtt_mock,
    make_entry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A pending confirmation must not fire after the entity is gone.

    The light is not optimistic, so the command leaves a three second timer
    behind that would adopt the level it sent. Removing the entity has to drop
    that timer; otherwise it fires into an entity Home Assistant has already
    forgotten.
    """
    entry = await setup_bridge(hass, make_entry, [SIMPLE])

    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": "light.kochnische", "brightness": 37},
        blocking=True,
    )
    # Nothing was adopted yet: the entity is still waiting for the gateway.
    assert hass.states.get("light.kochnische").state == "unknown"

    er.async_get(hass).async_remove("light.kochnische")
    await hass.async_block_till_done()
    assert hass.states.get("light.kochnische") is None

    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger="custom_components.casambi_lithernet"):
        async_fire_time_changed(
            hass, dt_util.utcnow() + timedelta(seconds=STATE_CONFIRM_TIMEOUT + 1)
        )
        await hass.async_block_till_done()

    # The timer is the only thing that can log this, and Home Assistant hides
    # the state write of a removed entity, so the log line is what makes a
    # surviving timer visible at all.
    assert not [
        record for record in caplog.records if "did not confirm" in record.getMessage()
    ]
    assert hass.states.get("light.kochnische") is None
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]
    assert entry.entry_id in hass.config_entries.async_entry_ids()
