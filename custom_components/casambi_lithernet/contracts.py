"""The interface every other package builds against.

:mod:`.gateway` provides the one real implementation; the config flow and
every platform see only this interface. :class:`tests.fake_gateway.FakeGateway`
is the second implementation, and ``tests/test_gateway_contract.py`` runs the
same assertions against both.

Rules that the implementation must honour, because the entities rely on them:

* **One command per call.** Turning a luminaire on with a brightness sends
  exactly one message. There is no separate "on" command that could overwrite a
  level or colour temperature afterwards.
* **One subscription per topic.** Several entities may watch the same unit; the
  gateway subscribes once and fans the message out.
* **Last known state is available immediately.** The gateway keeps the most
  recent message per topic, so a freshly added entity can render without
  waiting. Retained messages make this work across restarts.
* **Callbacks run in the event loop** and must not raise.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .const import TargetType
from .models import GatewayConfig
from .state import AggregateValues, SceneValues, UnitProperties, UnitValues

#: Called when a subscribed topic produces a new state. Never raises.
UnitValuesCallback = Callable[[UnitValues], None]
UnitPropertiesCallback = Callable[[UnitProperties], None]
AggregateCallback = Callable[[AggregateValues], None]
SceneCallback = Callable[[SceneValues], None]
AvailabilityCallback = Callable[[bool], None]

#: Returned by every subscribe call; calling it removes the subscription and,
#: when it was the last one for that topic, unsubscribes from MQTT.
Unsubscribe = Callable[[], None]


@dataclass(frozen=True, slots=True)
class CaptureResult:
    """Outcome of listening to the gateway for a while.

    Used by the config flow to tell the user whether state actually arrives and
    which kinds of message the installation produces.
    """

    seconds: float
    message_count: int
    unit_ids: tuple[int, ...] = ()
    group_ids: tuple[int, ...] = ()
    scene_ids: tuple[int, ...] = ()
    topic_kinds: tuple[str, ...] = ()

    @property
    def saw_any_state(self) -> bool:
        """Whether the gateway sent anything at all."""
        return self.message_count > 0

    @property
    def saw_device_elements(self) -> bool:
        """Whether ``poll_devicet`` messages arrived.

        These need Casambi Evolution 37.90 or newer on the units. Without them
        there are no per dimmer states, no sensors and no buttons.
        """
        return any("poll_devicet" in kind for kind in self.topic_kinds)

    @property
    def saw_buttons(self) -> bool:
        """Whether ``poll_button`` messages arrived."""
        return any("poll_button" in kind for kind in self.topic_kinds)


@dataclass(slots=True)
class GatewayDiagnostics:
    """Snapshot for the Home Assistant diagnostics download.

    Contains no credentials, because the integration never holds any: it talks
    through the MQTT integration.
    """

    commands_sent: int = 0
    messages_received: int = 0
    invalid_messages: int = 0
    last_message_per_topic: dict[str, dict[str, Any]] = field(default_factory=dict)
    subscribed_topics: tuple[str, ...] = ()


class CasambiGateway(ABC):
    """Talks to one gateway bridge over MQTT.

    One instance per config entry. Created in ``async_setup_entry`` and stored
    on the entry's runtime data.
    """

    config: GatewayConfig

    # -------------------------------------------------------- lifecycle ---

    @abstractmethod
    async def async_start(self) -> None:
        """Attach to MQTT and begin serving subscriptions."""

    @abstractmethod
    async def async_stop(self) -> None:
        """Drop all subscriptions and stop."""

    @property
    @abstractmethod
    def available(self) -> bool:
        """Whether the MQTT broker connection is usable."""

    @abstractmethod
    def subscribe_availability(self, callback: AvailabilityCallback) -> Unsubscribe:
        """Watch the broker connection so entities can follow it."""

    # --------------------------------------------------------- commands ---

    @abstractmethod
    async def async_set_level(
        self,
        target_type: TargetType,
        target_id: int,
        level: int,
        duration_ms: int | None = None,
    ) -> None:
        """Set a target to a brightness between 0 and 255.

        Level 0 switches off. This is the only command used for on, off and
        dimming; see the note about a single command in the module docstring.
        """

    @abstractmethod
    async def async_set_tc(
        self,
        target_type: TargetType,
        target_id: int,
        tc: int,
        duration_ms: int | None = None,
    ) -> None:
        """Set a target's colour temperature on the normalised 0-255 scale."""

    @abstractmethod
    async def async_set_dimmer(
        self,
        target_id: int,
        dimmer_index: int,
        value: int,
        duration_ms: int | None = None,
    ) -> None:
        """Set one DALI dimmer of a multi driver unit, index starting at 0."""

    @abstractmethod
    async def async_set_scene_level(
        self,
        scene_id: int,
        level: int,
        duration_ms: int | None = None,
    ) -> None:
        """Recall a scene at a brightness; level 0 switches the scene off."""

    @abstractmethod
    async def async_set_broadcast_level(
        self,
        level: int,
        duration_ms: int | None = None,
    ) -> None:
        """Set every luminaire in the network at once."""

    # ---------------------------------------------------- subscriptions ---

    @abstractmethod
    def subscribe_unit(self, unit_id: int, callback: UnitValuesCallback) -> Unsubscribe:
        """Watch ``poll_device/<id>/values``."""

    @abstractmethod
    def subscribe_unit_properties(
        self, unit_id: int, callback: UnitPropertiesCallback
    ) -> Unsubscribe:
        """Watch ``poll_device/<id>/propertys``."""

    @abstractmethod
    def subscribe_group(
        self, group_id: int, callback: AggregateCallback
    ) -> Unsubscribe:
        """Watch ``poll_group/<id>``. Group id 0 means the ungrouped ones."""

    @abstractmethod
    def subscribe_scene(self, scene_id: int, callback: SceneCallback) -> Unsubscribe:
        """Watch ``poll_scene/<id>``."""

    @abstractmethod
    def subscribe_broadcast(self, callback: AggregateCallback) -> Unsubscribe:
        """Watch ``poll_broadcast``."""

    # ------------------------------------------------- last known state ---

    @abstractmethod
    def unit_values(self, unit_id: int) -> UnitValues | None:
        """Most recent values for a unit, or None if nothing arrived yet."""

    @abstractmethod
    def unit_properties(self, unit_id: int) -> UnitProperties | None:
        """Most recent properties for a unit, or None."""

    @abstractmethod
    def group_values(self, group_id: int) -> AggregateValues | None:
        """Most recent values for a group, or None."""

    @abstractmethod
    def scene_values(self, scene_id: int) -> SceneValues | None:
        """Most recent values for a scene, or None."""

    @abstractmethod
    def broadcast_values(self) -> AggregateValues | None:
        """Most recent broadcast values, or None."""

    # ---------------------------------------------------- verification ----

    @abstractmethod
    async def async_blink_test(self, unit_id: int, seconds: float = 2.0) -> None:
        """Switch a unit to full brightness, wait, then switch it off.

        Used by the config flow so the user can confirm that commands arrive.
        """

    @abstractmethod
    async def async_capture(self, seconds: float) -> CaptureResult:
        """Listen to every gateway topic for a while and summarise what came."""

    # ----------------------------------------------------- diagnostics ----

    @abstractmethod
    def diagnostics(self) -> GatewayDiagnostics:
        """Snapshot for the diagnostics download."""
