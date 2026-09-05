"""MQTT gateway implementation.

The only implementation of
:class:`~.contracts.CasambiGateway`; entities never talk to MQTT themselves.

Three promises the rest of the integration builds on:

* **One command per call.** Every method here publishes exactly one message.
  There is deliberately no "switch on" command, because a second message with
  level 255 would overwrite the brightness or colour temperature sent with it
  (docs/DESIGN.md, "Der wichtigste Fallstrick").
* **One subscription per topic.** Several entities may watch the same unit;
  the gateway subscribes once, fans the message out and unsubscribes again
  when the last of them goes away.
* **Last known state is available immediately.** The most recent message per
  topic is kept, so an entity added later renders without waiting. The gateway
  publishes retained, so this survives a Home Assistant restart.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from homeassistant.components import mqtt
from homeassistant.core import HomeAssistant
from homeassistant.core import callback as ha_callback
from homeassistant.exceptions import HomeAssistantError

from .commands import (
    Command,
    Topics,
    broadcast_level,
    scene_level,
    target_dimmers,
    target_level,
    target_tc,
)
from .const import (
    GET_POLL_BUTTON,
    GET_POLL_DEVICE,
    GET_POLL_DEVICET,
    GET_POLL_GROUP,
    GET_POLL_SCENE,
    LEVEL_MAX,
    LEVEL_MIN,
    TOPIC_GET,
    TargetType,
)
from .contracts import (
    AggregateCallback,
    AvailabilityCallback,
    CaptureResult,
    CasambiGateway,
    GatewayDiagnostics,
    SceneCallback,
    UnitPropertiesCallback,
    UnitValuesCallback,
    Unsubscribe,
)
from .models import GatewayConfig
from .parser import (
    parse_aggregate_values,
    parse_scene_values,
    parse_unit_properties,
    parse_unit_values,
)
from .state import AggregateValues, SceneValues, UnitProperties, UnitValues

_LOGGER = logging.getLogger(__name__)

#: Placeholder a numeric topic segment is normalised to, so that the kinds a
#: capture reports stay countable ("poll_device/<id>/values").
ID_PLACEHOLDER = "<id>"

_NUMERIC = re.compile(r"^\d+$")

#: Parses a payload into one of the state objects, or returns None.
type Parse = Callable[[str | bytes], Any | None]


def create_gateway(hass: HomeAssistant, config: GatewayConfig) -> CasambiGateway:
    """Build the gateway object for one bridge."""
    return MqttCasambiGateway(hass, config)


@dataclass(slots=True)
class _TopicSubscription:
    """One MQTT subscription serving any number of entities."""

    topic: str
    parse: Parse
    listeners: dict[object, Callable[[Any], None]] = field(default_factory=dict)
    remove: Callable[[], None] | None = None
    detached: bool = False
    failed: bool = False


class MqttCasambiGateway(CasambiGateway):
    """Talks to one gateway bridge through the Home Assistant MQTT integration."""

    def __init__(self, hass: HomeAssistant, config: GatewayConfig) -> None:
        """Prepare the topics of one bridge; nothing is subscribed yet."""
        self.hass = hass
        self.config = config
        self.topics = Topics(config.topic_prefix, config.bridge_id)
        self._subscriptions: dict[str, _TopicSubscription] = {}
        self._last_state: dict[str, Any] = {}
        self._last_message: dict[str, dict[str, Any]] = {}
        self._warned_topics: set[str] = set()
        self._commands_sent = 0
        self._messages_received = 0
        self._invalid_messages = 0
        self._started = False

    # -------------------------------------------------------- lifecycle ---

    async def async_start(self) -> None:
        """Attach to MQTT and begin serving subscriptions."""
        self._started = True
        _LOGGER.debug("Gateway for bridge %s started", self.config.bridge_id)

    async def async_stop(self) -> None:
        """Drop all subscriptions and stop."""
        self._started = False
        for subscription in list(self._subscriptions.values()):
            subscription.listeners.clear()
            self._detach(subscription)
        self._subscriptions.clear()
        _LOGGER.debug("Gateway for bridge %s stopped", self.config.bridge_id)

    @property
    def available(self) -> bool:
        """Whether the MQTT broker connection is usable."""
        if not self._started:
            return False
        try:
            return mqtt.is_connected(self.hass)
        except KeyError:
            return False

    def subscribe_availability(self, callback: AvailabilityCallback) -> Unsubscribe:
        """Watch the broker connection so entities can follow it."""

        @ha_callback
        def _forward(connected: bool) -> None:
            _call_safely(callback, connected)

        return mqtt.async_subscribe_connection_status(self.hass, _forward)

    # --------------------------------------------------------- commands ---

    async def async_set_level(
        self,
        target_type: TargetType,
        target_id: int,
        level: int,
        duration_ms: int | None = None,
    ) -> None:
        """Set a target to a brightness between 0 and 255.

        Level 0 switches off; this is the only command used for on, off and
        dimming.
        """
        await self._send(
            target_level(
                self.topics, target_type, target_id, level, self._duration(duration_ms)
            )
        )

    async def async_set_tc(
        self,
        target_type: TargetType,
        target_id: int,
        tc: int,
        duration_ms: int | None = None,
    ) -> None:
        """Set a target's colour temperature on the normalised 0-255 scale."""
        await self._send(
            target_tc(
                self.topics, target_type, target_id, tc, self._duration(duration_ms)
            )
        )

    async def async_set_dimmer(
        self,
        target_id: int,
        dimmer_index: int,
        value: int,
        duration_ms: int | None = None,
    ) -> None:
        """Set one DALI dimmer of a multi driver unit, index starting at 0."""
        await self._send(
            target_dimmers(
                self.topics,
                target_id,
                dimmer_index,
                value,
                self._duration(duration_ms),
            )
        )

    async def async_set_scene_level(
        self,
        scene_id: int,
        level: int,
        duration_ms: int | None = None,
    ) -> None:
        """Recall a scene at a brightness; level 0 switches the scene off."""
        await self._send(
            scene_level(self.topics, scene_id, level, self._duration(duration_ms))
        )

    async def async_set_broadcast_level(
        self,
        level: int,
        duration_ms: int | None = None,
    ) -> None:
        """Set every luminaire in the network at once."""
        await self._send(
            broadcast_level(self.topics, level, self._duration(duration_ms))
        )

    # ---------------------------------------------------- subscriptions ---

    def subscribe_unit(self, unit_id: int, callback: UnitValuesCallback) -> Unsubscribe:
        """Watch ``poll_device/<id>/values``."""
        return self._subscribe(
            self.topics.unit_values(unit_id), parse_unit_values, callback
        )

    def subscribe_unit_properties(
        self, unit_id: int, callback: UnitPropertiesCallback
    ) -> Unsubscribe:
        """Watch ``poll_device/<id>/propertys``."""
        return self._subscribe(
            self.topics.unit_properties(unit_id), parse_unit_properties, callback
        )

    def subscribe_group(
        self, group_id: int, callback: AggregateCallback
    ) -> Unsubscribe:
        """Watch ``poll_group/<id>``. Group id 0 means the ungrouped ones."""
        return self._subscribe(
            self.topics.group(group_id), parse_aggregate_values, callback
        )

    def subscribe_scene(self, scene_id: int, callback: SceneCallback) -> Unsubscribe:
        """Watch ``poll_scene/<id>``."""
        return self._subscribe(
            self.topics.scene(scene_id), parse_scene_values, callback
        )

    def subscribe_broadcast(self, callback: AggregateCallback) -> Unsubscribe:
        """Watch ``poll_broadcast``."""
        return self._subscribe(
            self.topics.broadcast(), parse_aggregate_values, callback
        )

    # ------------------------------------------------- last known state ---

    def unit_values(self, unit_id: int) -> UnitValues | None:
        """Most recent values for a unit, or None if nothing arrived yet."""
        return self._known(self.topics.unit_values(unit_id), UnitValues)

    def unit_properties(self, unit_id: int) -> UnitProperties | None:
        """Most recent properties for a unit, or None."""
        return self._known(self.topics.unit_properties(unit_id), UnitProperties)

    def group_values(self, group_id: int) -> AggregateValues | None:
        """Most recent values for a group, or None."""
        return self._known(self.topics.group(group_id), AggregateValues)

    def scene_values(self, scene_id: int) -> SceneValues | None:
        """Most recent values for a scene, or None."""
        return self._known(self.topics.scene(scene_id), SceneValues)

    def broadcast_values(self) -> AggregateValues | None:
        """Most recent broadcast values, or None."""
        return self._known(self.topics.broadcast(), AggregateValues)

    # ---------------------------------------------------- verification ----

    async def async_blink_test(self, unit_id: int, seconds: float = 2.0) -> None:
        """Switch a unit to full brightness, wait, then switch it off.

        The switching off happens even when the wait is cancelled, for example
        because the user closed the setup dialog. Otherwise the luminaire the
        user picked for the test would be left at full brightness with nothing
        to tell them why.
        """
        await self.async_set_level(TargetType.UNIT, unit_id, LEVEL_MAX)
        try:
            await asyncio.sleep(seconds)
        finally:
            await self.async_set_level(TargetType.UNIT, unit_id, LEVEL_MIN)

    async def async_capture(self, seconds: float) -> CaptureResult:
        """Listen to every gateway topic for a while and summarise what came."""
        capture = _Capture()
        remove = await mqtt.async_subscribe(
            self.hass, self.topics.all_states(), capture.handle
        )
        try:
            await asyncio.sleep(seconds)
        finally:
            remove()
        return capture.result(seconds)

    # ----------------------------------------------------- diagnostics ----

    def diagnostics(self) -> GatewayDiagnostics:
        """Snapshot for the diagnostics download."""
        return GatewayDiagnostics(
            commands_sent=self._commands_sent,
            messages_received=self._messages_received,
            invalid_messages=self._invalid_messages,
            last_message_per_topic=dict(self._last_message),
            subscribed_topics=tuple(
                sorted(
                    topic
                    for topic, subscription in self._subscriptions.items()
                    if not subscription.failed
                )
            ),
        )

    # -------------------------------------------------------- internals ---

    def _duration(self, duration_ms: int | None) -> int:
        """Fall back to the configured transition time when none is given."""
        if duration_ms is None:
            return self.config.default_duration_ms
        return int(duration_ms)

    async def _send(self, command: Command) -> None:
        """Publish one command and count it."""
        payload = json.dumps(command.payload)
        _LOGGER.debug("Sending %s %s", command.topic, payload)
        await mqtt.async_publish(self.hass, command.topic, payload)
        self._commands_sent += 1

    def _known[StateT](self, topic: str, expected: type[StateT]) -> StateT | None:
        """Return the last state of a topic when it has the expected type."""
        state = self._last_state.get(topic)
        return state if isinstance(state, expected) else None

    def _subscribe(
        self, topic: str, parse: Parse, callback: Callable[[Any], None]
    ) -> Unsubscribe:
        """Add one listener to a topic, subscribing to MQTT on the first one."""
        subscription = self._subscriptions.get(topic)
        if subscription is None:
            subscription = _TopicSubscription(topic=topic, parse=parse)
            self._subscriptions[topic] = subscription
            self.hass.async_create_task(self._async_attach(subscription))
        token = object()
        subscription.listeners[token] = callback

        def _unsubscribe() -> None:
            subscription.listeners.pop(token, None)
            if subscription.listeners:
                return
            if self._subscriptions.get(topic) is subscription:
                del self._subscriptions[topic]
            self._detach(subscription)

        return _unsubscribe

    async def _async_attach(self, subscription: _TopicSubscription) -> None:
        """Subscribe to MQTT, unless the last listener left in the meantime."""

        @ha_callback
        def _message(message: mqtt.ReceiveMessage) -> None:
            self._handle_message(subscription, message)

        try:
            remove = await mqtt.async_subscribe(self.hass, subscription.topic, _message)
        except HomeAssistantError:
            # Nothing above us is awaiting this task, so an exception would
            # vanish into the log as "never retrieved" and the topic would look
            # subscribed while no message ever arrives again.
            subscription.failed = True
            _LOGGER.error(
                "Could not subscribe to %s; entities on this topic will not "
                "report their state until the entry is reloaded",
                subscription.topic,
                exc_info=True,
            )
            return
        if subscription.detached:
            remove()
            return
        subscription.remove = remove
        _LOGGER.debug("Subscribed to %s", subscription.topic)

    def _detach(self, subscription: _TopicSubscription) -> None:
        """Drop the MQTT subscription of a topic nobody listens to any more."""
        subscription.detached = True
        if subscription.remove is not None:
            subscription.remove()
            subscription.remove = None
            _LOGGER.debug("Unsubscribed from %s", subscription.topic)

    @ha_callback
    def _handle_message(
        self, subscription: _TopicSubscription, message: mqtt.ReceiveMessage
    ) -> None:
        """Parse one message and hand it to everybody watching the topic."""
        self._messages_received += 1
        raw = message.payload
        # decode() takes str and bytes; str(bytes) would produce a repr that
        # never parses.
        parsed = subscription.parse(raw if isinstance(raw, (str, bytes)) else str(raw))
        if parsed is None:
            self._invalid_messages += 1
            self._log_invalid(subscription.topic, raw)
            return

        self._last_state[subscription.topic] = parsed
        self._last_message[subscription.topic] = {
            "payload": raw,
            "received": datetime.now(UTC).isoformat(),
            "retained": message.retain,
        }
        _LOGGER.debug("Received %s %s", subscription.topic, raw)
        for listener in list(subscription.listeners.values()):
            _call_safely(listener, parsed)

    def _log_invalid(self, topic: str, raw: object) -> None:
        """Warn about a bad payload once per topic, then stay on debug."""
        if topic in self._warned_topics:
            _LOGGER.debug("Discarding another unusable message on %s: %s", topic, raw)
            return
        self._warned_topics.add(topic)
        _LOGGER.warning(
            "Discarding unusable message on %s: %s. Further ones are logged on "
            "debug level only",
            topic,
            raw,
        )


class _Capture:
    """Collects what arrives on the bridge during :meth:`async_capture`."""

    def __init__(self) -> None:
        """Start with nothing seen."""
        self.count = 0
        self.unit_ids: set[int] = set()
        self.group_ids: set[int] = set()
        self.scene_ids: set[int] = set()
        self.kinds: set[str] = set()

    @ha_callback
    def handle(self, message: mqtt.ReceiveMessage) -> None:
        """Note one message without parsing its payload."""
        self.count += 1
        segments = message.topic.split("/")
        if TOPIC_GET not in segments:
            return
        tail = segments[segments.index(TOPIC_GET) + 1 :]
        if not tail:
            return
        self.kinds.add("/".join(_NUMERIC.sub(ID_PLACEHOLDER, part) for part in tail))
        self._note_id(tail)

    def _note_id(self, tail: list[str]) -> None:
        """Remember the address a state topic belongs to."""
        if len(tail) < 2 or not _NUMERIC.match(tail[1]):
            return
        number = int(tail[1])
        if tail[0] in (GET_POLL_DEVICE, GET_POLL_DEVICET, GET_POLL_BUTTON):
            self.unit_ids.add(number)
        elif tail[0] == GET_POLL_GROUP:
            self.group_ids.add(number)
        elif tail[0] == GET_POLL_SCENE:
            self.scene_ids.add(number)

    def result(self, seconds: float) -> CaptureResult:
        """Turn what was seen into the result the config flow shows."""
        return CaptureResult(
            seconds=seconds,
            message_count=self.count,
            unit_ids=tuple(sorted(self.unit_ids)),
            group_ids=tuple(sorted(self.group_ids)),
            scene_ids=tuple(sorted(self.scene_ids)),
            topic_kinds=tuple(sorted(self.kinds)),
        )


def _call_safely(listener: Callable[[Any], None], value: Any) -> None:
    """Run a callback in the event loop without letting it break the fan out."""
    try:
        listener(value)
    except Exception:  # one bad entity must not stop the others
        _LOGGER.exception("Casambi listener raised on %r", value)
