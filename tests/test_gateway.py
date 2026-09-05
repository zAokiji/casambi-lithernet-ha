"""Package B: the MQTT gateway object."""

from __future__ import annotations

import asyncio
import logging

import pytest
from homeassistant.core import HomeAssistant

from custom_components.casambi_lithernet import gateway as gateway_module
from custom_components.casambi_lithernet.const import TargetType
from custom_components.casambi_lithernet.gateway import create_gateway
from custom_components.casambi_lithernet.models import GatewayConfig
from custom_components.casambi_lithernet.state import (
    AggregateValues,
    SceneValues,
    UnitProperties,
    UnitValues,
)

UNIT_VALUES_TOPIC = "casambi/0/get/poll_device/19/values"
UNIT_PROPERTIES_TOPIC = "casambi/0/get/poll_device/19/propertys"


@pytest.fixture
async def gateway(hass: HomeAssistant, mqtt_mock):
    """Provide a started gateway for bridge 0."""
    instance = create_gateway(hass, GatewayConfig())
    await instance.async_start()
    yield instance
    await instance.async_stop()


@pytest.fixture
def mqtt_topics(monkeypatch):
    """Record which topics were subscribed and unsubscribed at MQTT level.

    The real subscription still happens, so injected messages keep arriving.
    """
    real = gateway_module.mqtt.async_subscribe
    subscribed: list[str] = []
    unsubscribed: list[str] = []

    async def _spy(hass, topic, msg_callback, *args, **kwargs):
        subscribed.append(topic)
        remove = await real(hass, topic, msg_callback, *args, **kwargs)

        def _remove() -> None:
            unsubscribed.append(topic)
            remove()

        return _remove

    monkeypatch.setattr(gateway_module.mqtt, "async_subscribe", _spy)
    return subscribed, unsubscribed


# ------------------------------------------------------------- commands ---


async def test_set_level_sends_exactly_one_message(gateway, publish_log) -> None:
    """Switching on with a brightness is one command, never two."""
    await gateway.async_set_level(TargetType.UNIT, 19, 128)
    assert publish_log() == [
        (
            "casambi/0/set/target_level",
            {"level": 128, "duration": 0, "targetid": 19, "targettype": 1},
        )
    ]


async def test_set_tc_sends_exactly_one_message(gateway, publish_log) -> None:
    """A colour temperature is not accompanied by an on command."""
    await gateway.async_set_tc(TargetType.UNIT, 16, 128)
    assert publish_log() == [
        (
            "casambi/0/set/target_tc",
            {"tc": 128, "duration": 0, "targetid": 16, "targettype": 1},
        )
    ]


async def test_every_command_reaches_its_topic(gateway, publish_log) -> None:
    """Dimmer, scene and broadcast each publish once, on their own topic."""
    await gateway.async_set_dimmer(15, 1, 64)
    await gateway.async_set_scene_level(3, 200)
    await gateway.async_set_broadcast_level(0)
    assert [topic for topic, _ in publish_log()] == [
        "casambi/0/set/target_dimmers",
        "casambi/0/set/scene_level",
        "casambi/0/set/level",
    ]


async def test_duration_defaults_to_the_configured_value(
    hass: HomeAssistant, mqtt_mock, publish_log
) -> None:
    """Without a duration the gateway sends the configured transition time."""
    instance = create_gateway(hass, GatewayConfig(default_duration_ms=800))
    await instance.async_start()
    await instance.async_set_level(TargetType.GROUP, 2, 255)
    await instance.async_set_level(TargetType.GROUP, 2, 255, duration_ms=100)
    assert [payload["duration"] for _, payload in publish_log()] == [800, 100]
    await instance.async_stop()


async def test_blink_test_ends_dark(gateway, publish_log) -> None:
    """The blink test switches on, waits and switches off again."""
    await gateway.async_blink_test(19, seconds=0)
    assert [payload["level"] for _, payload in publish_log()] == [255, 0]


# -------------------------------------------------------- subscriptions ---


async def test_two_entities_share_one_subscription(
    hass: HomeAssistant, gateway, mqtt_topics, feed
) -> None:
    """A topic several entities watch is subscribed exactly once."""
    subscribed, _ = mqtt_topics
    first: list[UnitValues] = []
    second: list[UnitValues] = []
    gateway.subscribe_unit(19, first.append)
    gateway.subscribe_unit(19, second.append)
    await hass.async_block_till_done()

    assert subscribed.count(UNIT_VALUES_TOPIC) == 1

    await feed(UNIT_VALUES_TOPIC, {"level": 42})
    assert [values.level for values in first] == [42]
    assert [values.level for values in second] == [42]


async def test_last_subscriber_tears_the_subscription_down(
    hass: HomeAssistant, gateway, mqtt_topics
) -> None:
    """MQTT is left alone again once nobody watches the topic."""
    subscribed, unsubscribed = mqtt_topics
    remove_first = gateway.subscribe_unit(19, lambda _values: None)
    remove_second = gateway.subscribe_unit(19, lambda _values: None)
    await hass.async_block_till_done()

    remove_first()
    assert unsubscribed == []

    remove_second()
    assert unsubscribed == [UNIT_VALUES_TOPIC]

    gateway.subscribe_unit(19, lambda _values: None)
    await hass.async_block_till_done()
    assert subscribed.count(UNIT_VALUES_TOPIC) == 2


async def test_stopping_drops_every_subscription(
    hass: HomeAssistant, mqtt_mock, mqtt_topics
) -> None:
    """Unloading the entry leaves no subscription behind."""
    _, unsubscribed = mqtt_topics
    instance = create_gateway(hass, GatewayConfig())
    await instance.async_start()
    instance.subscribe_unit(19, lambda _values: None)
    instance.subscribe_broadcast(lambda _values: None)
    await hass.async_block_till_done()

    await instance.async_stop()
    assert sorted(unsubscribed) == [
        "casambi/0/get/poll_broadcast",
        UNIT_VALUES_TOPIC,
    ]
    assert instance.diagnostics().subscribed_topics == ()


async def test_group_zero_watches_the_ungrouped_topic(
    hass: HomeAssistant, gateway, mqtt_topics
) -> None:
    """Group id 0 means the ungrouped luminaires, on their own topic."""
    subscribed, _ = mqtt_topics
    gateway.subscribe_group(0, lambda _values: None)
    gateway.subscribe_group(2, lambda _values: None)
    await hass.async_block_till_done()
    assert subscribed == ["casambi/0/get/poll_ungrouped", "casambi/0/get/poll_group/2"]


async def test_a_raising_listener_does_not_stop_the_others(
    hass: HomeAssistant, gateway, feed
) -> None:
    """One broken entity must not cost the other entities their update."""
    seen: list[UnitValues] = []

    def _boom(_values: UnitValues) -> None:
        raise RuntimeError("entity is broken")

    gateway.subscribe_unit(19, _boom)
    gateway.subscribe_unit(19, seen.append)
    await hass.async_block_till_done()

    await feed(UNIT_VALUES_TOPIC, {"level": 7})
    assert [values.level for values in seen] == [7]


# ----------------------------------------------------- last known state ---


async def test_last_known_state_is_served_from_memory(
    hass: HomeAssistant, gateway, feed, payload
) -> None:
    """Every kind of state is remembered per topic and given out again."""
    gateway.subscribe_unit(19, lambda _values: None)
    gateway.subscribe_unit_properties(19, lambda _values: None)
    gateway.subscribe_group(2, lambda _values: None)
    gateway.subscribe_scene(1, lambda _values: None)
    gateway.subscribe_broadcast(lambda _values: None)
    await hass.async_block_till_done()

    await feed(UNIT_VALUES_TOPIC, payload("unit_values_on"))
    await feed(UNIT_PROPERTIES_TOPIC, payload("unit_properties_online"))
    await feed("casambi/0/get/poll_group/2", payload("group_values_on"))
    await feed("casambi/0/get/poll_scene/1", payload("scene_values_active"))
    await feed("casambi/0/get/poll_broadcast", payload("broadcast_values"))

    assert isinstance(gateway.unit_values(19), UnitValues)
    assert gateway.unit_values(19).level == 4
    assert isinstance(gateway.unit_properties(19), UnitProperties)
    assert gateway.unit_properties(19).online is True
    assert isinstance(gateway.group_values(2), AggregateValues)
    assert gateway.group_values(2).level == 128
    assert isinstance(gateway.scene_values(1), SceneValues)
    assert gateway.scene_values(1).is_on
    assert isinstance(gateway.broadcast_values(), AggregateValues)
    assert gateway.broadcast_values().level == 31


async def test_unknown_state_is_none(gateway) -> None:
    """Nothing heard yet means None, not a made up value."""
    assert gateway.unit_values(19) is None
    assert gateway.unit_properties(19) is None
    assert gateway.group_values(2) is None
    assert gateway.scene_values(1) is None
    assert gateway.broadcast_values() is None


async def test_state_survives_the_last_entity(
    hass: HomeAssistant, gateway, feed
) -> None:
    """A reloaded entity finds the state its predecessor received."""
    remove = gateway.subscribe_unit(19, lambda _values: None)
    await hass.async_block_till_done()
    await feed(UNIT_VALUES_TOPIC, {"level": 99})
    remove()
    assert gateway.unit_values(19).level == 99


# ---------------------------------------------------- invalid messages ---


async def test_invalid_message_is_dropped_and_warned_once(
    hass: HomeAssistant, gateway, feed, caplog
) -> None:
    """A bad payload is discarded, warned about once, then only on debug."""
    seen: list[UnitValues] = []
    gateway.subscribe_unit(19, seen.append)
    await hass.async_block_till_done()

    with caplog.at_level(logging.DEBUG):
        await feed(UNIT_VALUES_TOPIC, "not json at all")
        await feed(UNIT_VALUES_TOPIC, '{"nothing":1}')

    warnings = [
        record
        for record in caplog.records
        if record.levelno == logging.WARNING
        and UNIT_VALUES_TOPIC in record.getMessage()
    ]
    assert len(warnings) == 1
    assert seen == []
    assert gateway.unit_values(19) is None
    assert gateway.diagnostics().invalid_messages == 2


async def test_each_topic_warns_on_its_own(
    hass: HomeAssistant, gateway, feed, caplog
) -> None:
    """The warning is silenced per topic, not for the whole gateway."""
    gateway.subscribe_unit(19, lambda _values: None)
    gateway.subscribe_unit(20, lambda _values: None)
    await hass.async_block_till_done()

    await feed(UNIT_VALUES_TOPIC, "broken")
    await feed("casambi/0/get/poll_device/20/values", "broken")

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 2


# ------------------------------------------------------------- capture ---


async def test_capture_reports_ids_and_topic_kinds(
    hass: HomeAssistant, gateway, feed
) -> None:
    """A capture says what arrived, in the shape the config flow reads."""
    task = asyncio.ensure_future(gateway.async_capture(0.05))
    await asyncio.sleep(0)
    await feed(UNIT_VALUES_TOPIC, {"level": 1})
    await feed(UNIT_PROPERTIES_TOPIC, {"online": 1})
    await feed("casambi/0/get/poll_group/2", {"level": 0})
    await feed("casambi/0/get/poll_scene/1", {"active": 0, "level": 255})
    await feed("casambi/0/get/poll_broadcast", {"level": 3})
    await feed("casambi/0/get/node_deleted/", {"device": 7})
    result = await task

    assert result.saw_any_state
    assert result.message_count == 6
    assert result.unit_ids == (19,)
    assert result.group_ids == (2,)
    assert result.scene_ids == (1,)
    assert "poll_device/<id>/values" in result.topic_kinds
    assert "poll_broadcast" in result.topic_kinds
    assert not result.saw_device_elements
    assert not result.saw_buttons


async def test_capture_recognises_element_and_button_topics(
    hass: HomeAssistant, gateway, feed
) -> None:
    """The firmware dependent topics are reported the way the flow tests them."""
    task = asyncio.ensure_future(gateway.async_capture(0.05))
    await asyncio.sleep(0)
    await feed("casambi/0/get/poll_devicet/16/element_dimmer", {"dimmer_1": 10})
    await feed("casambi/0/get/poll_button/8", {"button_type": 1})
    result = await task

    assert result.saw_device_elements
    assert result.saw_buttons
    assert result.unit_ids == (8, 16)


async def test_capture_without_traffic_is_empty(gateway) -> None:
    """A silent gateway produces a result that says so."""
    result = await gateway.async_capture(0.01)
    assert not result.saw_any_state
    assert result.topic_kinds == ()


# --------------------------------------------------------- diagnostics ---


async def test_diagnostics_counts_and_remembers(
    hass: HomeAssistant, gateway, feed
) -> None:
    """The download shows what was sent, received and subscribed."""
    gateway.subscribe_unit(19, lambda _values: None)
    await hass.async_block_till_done()
    await feed(UNIT_VALUES_TOPIC, {"level": 12})
    await gateway.async_set_level(TargetType.UNIT, 19, 12)

    diagnostics = gateway.diagnostics()
    assert diagnostics.commands_sent == 1
    assert diagnostics.messages_received == 1
    assert diagnostics.invalid_messages == 0
    assert diagnostics.subscribed_topics == (UNIT_VALUES_TOPIC,)
    last = diagnostics.last_message_per_topic[UNIT_VALUES_TOPIC]
    assert last["payload"] == '{"level": 12}'
    assert "received" in last


# -------------------------------------------------------- availability ---


async def test_availability_follows_the_broker(gateway) -> None:
    """A started gateway with a connected broker is available."""
    assert gateway.available is True


async def test_availability_is_false_before_start(
    hass: HomeAssistant, mqtt_mock
) -> None:
    """Nothing is available before the entry finished setting up."""
    assert create_gateway(hass, GatewayConfig()).available is False


async def test_subscribe_availability_can_be_removed(gateway) -> None:
    """Watching the connection is optional and can be stopped again."""
    seen: list[bool] = []
    remove = gateway.subscribe_availability(seen.append)
    remove()
