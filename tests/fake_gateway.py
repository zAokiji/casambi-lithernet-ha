"""Shared test doubles: an in-memory gateway and the payload helpers.

Owned by package H. This code used to live inside ``test_entity.py``, which
made four other test modules import from a test file. Everything more than one
test module needs now lives here; ``test_entity.py`` re-exports it so the older
imports keep working.

What is in here:

* :class:`FakeGateway` — an in-memory implementation of the contract in
  :mod:`custom_components.casambi_lithernet.contracts`. It records every
  command instead of publishing it and lets a test push state as if the
  gateway had published it.
* ``push_*`` methods deliver a message to the entities watching a topic,
  ``seed_*`` methods pretend a retained message was already on the broker
  before anybody subscribed. Both exist for units, groups, scenes and
  broadcast.
* ``*_values_from`` functions turn a recorded fixture payload into the state
  object the real parser would have produced, so a test asserts against real
  gateway data without going through MQTT.
* :func:`make_setup_units` builds the body of the ``setup_units`` fixture: it
  creates a config entry with the given elements and patches
  :func:`~custom_components.casambi_lithernet.create_gateway` so the entry runs
  on the fake.

For a test that wants the *real* gateway with mocked MQTT instead, see
``test_end_to_end.py``.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.casambi_lithernet.const import TargetType
from custom_components.casambi_lithernet.contracts import (
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
from custom_components.casambi_lithernet.models import GatewayConfig, UnitDefinition
from custom_components.casambi_lithernet.state import (
    AggregateValues,
    SceneValues,
    UnitProperties,
    UnitValues,
)


@dataclass(frozen=True)
class Command:
    """One command the entity asked the gateway to send."""

    kind: str
    target_type: TargetType | None
    target_id: int
    value: int
    duration_ms: int | None
    index: int | None = None


class FakeGateway(CasambiGateway):
    """In memory stand-in for the real gateway of package B."""

    def __init__(self, config: GatewayConfig) -> None:
        """Start with no state, no subscribers and a connected broker."""
        self.config = config
        self.commands: list[Command] = []
        self._available = True
        self._unit_values: dict[int, UnitValues] = {}
        self._unit_properties: dict[int, UnitProperties] = {}
        self._group_values: dict[int, AggregateValues] = {}
        self._scene_values: dict[int, SceneValues] = {}
        self._broadcast: AggregateValues | None = None
        self._availability_subs: list[AvailabilityCallback] = []
        self._unit_subs: dict[int, list[UnitValuesCallback]] = {}
        self._property_subs: dict[int, list[UnitPropertiesCallback]] = {}
        self._group_subs: dict[int, list[AggregateCallback]] = {}
        self._scene_subs: dict[int, list[SceneCallback]] = {}
        self._broadcast_subs: list[AggregateCallback] = []

    # ------------------------------------------------------------ helpers --

    @property
    def levels(self) -> list[int]:
        """Levels of the ``target_level`` commands, in order."""
        return [c.value for c in self.commands if c.kind == "level"]

    def push_unit_values(self, unit_id: int, values: UnitValues) -> None:
        """Deliver a values message to every subscriber."""
        self._unit_values[unit_id] = values
        for callback in list(self._unit_subs.get(unit_id, ())):
            callback(values)

    def push_unit_properties(self, unit_id: int, properties: UnitProperties) -> None:
        """Deliver a propertys message to every subscriber."""
        self._unit_properties[unit_id] = properties
        for callback in list(self._property_subs.get(unit_id, ())):
            callback(properties)

    def push_group_values(self, group_id: int, values: AggregateValues) -> None:
        """Deliver a group message to every subscriber."""
        self._group_values[group_id] = values
        for callback in list(self._group_subs.get(group_id, ())):
            callback(values)

    def push_scene_values(self, scene_id: int, values: SceneValues) -> None:
        """Deliver a scene message to every subscriber."""
        self._scene_values[scene_id] = values
        for callback in list(self._scene_subs.get(scene_id, ())):
            callback(values)

    def push_broadcast_values(self, values: AggregateValues) -> None:
        """Deliver a broadcast message to every subscriber."""
        self._broadcast = values
        for callback in list(self._broadcast_subs):
            callback(values)

    def set_available(self, available: bool) -> None:
        """Report a broker connection change."""
        self._available = available
        for callback in list(self._availability_subs):
            callback(available)

    def seed_unit_values(self, unit_id: int, values: UnitValues) -> None:
        """Pretend a retained message was already on the broker."""
        self._unit_values[unit_id] = values

    def seed_unit_properties(self, unit_id: int, properties: UnitProperties) -> None:
        """Pretend a retained propertys message was already on the broker."""
        self._unit_properties[unit_id] = properties

    def seed_group_values(self, group_id: int, values: AggregateValues) -> None:
        """Pretend a group message was already known before subscribing."""
        self._group_values[group_id] = values

    def seed_scene_values(self, scene_id: int, values: SceneValues) -> None:
        """Pretend a scene message was already known before subscribing."""
        self._scene_values[scene_id] = values

    def seed_broadcast_values(self, values: AggregateValues) -> None:
        """Pretend a broadcast message was already known before subscribing."""
        self._broadcast = values

    # ---------------------------------------------------------- lifecycle --

    async def async_start(self) -> None:
        """Nothing to attach to."""
        return None

    async def async_stop(self) -> None:
        """Nothing to detach."""
        return None

    @property
    def available(self) -> bool:
        """Whether the fake broker is connected."""
        return self._available

    def subscribe_availability(self, callback: AvailabilityCallback) -> Unsubscribe:
        """Watch the fake broker connection."""
        return _append(self._availability_subs, callback)

    # ----------------------------------------------------------- commands --

    async def async_set_level(
        self,
        target_type: TargetType,
        target_id: int,
        level: int,
        duration_ms: int | None = None,
    ) -> None:
        """Record a level command."""
        self.commands.append(
            Command("level", target_type, target_id, level, duration_ms)
        )

    async def async_set_tc(
        self,
        target_type: TargetType,
        target_id: int,
        tc: int,
        duration_ms: int | None = None,
    ) -> None:
        """Record a colour temperature command."""
        self.commands.append(Command("tc", target_type, target_id, tc, duration_ms))

    async def async_set_dimmer(
        self,
        target_id: int,
        dimmer_index: int,
        value: int,
        duration_ms: int | None = None,
    ) -> None:
        """Record a dimmer command."""
        self.commands.append(
            Command("dimmer", None, target_id, value, duration_ms, dimmer_index)
        )

    async def async_set_scene_level(
        self, scene_id: int, level: int, duration_ms: int | None = None
    ) -> None:
        """Record a scene command."""
        self.commands.append(Command("scene", None, scene_id, level, duration_ms))

    async def async_set_broadcast_level(
        self, level: int, duration_ms: int | None = None
    ) -> None:
        """Record a broadcast command."""
        self.commands.append(Command("broadcast", None, 0, level, duration_ms))

    # ------------------------------------------------------ subscriptions --

    def subscribe_unit(self, unit_id: int, callback: UnitValuesCallback) -> Unsubscribe:
        """Watch a unit's values."""
        return _append(self._unit_subs.setdefault(unit_id, []), callback)

    def subscribe_unit_properties(
        self, unit_id: int, callback: UnitPropertiesCallback
    ) -> Unsubscribe:
        """Watch a unit's properties."""
        return _append(self._property_subs.setdefault(unit_id, []), callback)

    def subscribe_group(
        self, group_id: int, callback: AggregateCallback
    ) -> Unsubscribe:
        """Watch a group."""
        return _append(self._group_subs.setdefault(group_id, []), callback)

    def subscribe_scene(self, scene_id: int, callback: SceneCallback) -> Unsubscribe:
        """Watch a scene."""
        return _append(self._scene_subs.setdefault(scene_id, []), callback)

    def subscribe_broadcast(self, callback: AggregateCallback) -> Unsubscribe:
        """Watch the broadcast topic."""
        return _append(self._broadcast_subs, callback)

    # -------------------------------------------------- last known state --

    def unit_values(self, unit_id: int) -> UnitValues | None:
        """Last known values of a unit."""
        return self._unit_values.get(unit_id)

    def unit_properties(self, unit_id: int) -> UnitProperties | None:
        """Last known properties of a unit."""
        return self._unit_properties.get(unit_id)

    def group_values(self, group_id: int) -> AggregateValues | None:
        """Last known values of a group."""
        return self._group_values.get(group_id)

    def scene_values(self, scene_id: int) -> SceneValues | None:
        """Last known values of a scene."""
        return self._scene_values.get(scene_id)

    def broadcast_values(self) -> AggregateValues | None:
        """Last known broadcast values."""
        return self._broadcast

    # ------------------------------------------------------ verification --

    async def async_blink_test(self, unit_id: int, seconds: float = 2.0) -> None:
        """Do nothing visible."""
        return None

    async def async_capture(self, seconds: float) -> CaptureResult:
        """Report an empty capture."""
        return CaptureResult(seconds=seconds, message_count=0)

    def diagnostics(self) -> GatewayDiagnostics:
        """Report empty diagnostics."""
        return GatewayDiagnostics()


def _append(target: list[Any], callback: Any) -> Unsubscribe:
    """Add a subscriber and return the matching unsubscribe."""
    target.append(callback)

    def _remove() -> None:
        if callback in target:
            target.remove(callback)

    return _remove


# ---------------------------------------------------------------- helpers --


def unit_values_from(raw: str) -> UnitValues:
    """Build a state object from a recorded ``values`` payload."""
    data = json.loads(raw)
    return UnitValues(
        level=data["level"],
        last_level=data["last_level"],
        cct_level=data["cct_level"],
        scene=data["scene"],
        vertical=data["vertical"],
        last_change=data["last_change"],
    )


def unit_properties_from(raw: str) -> UnitProperties:
    """Build a properties object from a recorded ``propertys`` payload."""
    data = json.loads(raw)
    return UnitProperties(
        online=bool(data["online"]),
        node_type=data["node_type"],
        priority_raw=data["priority"],
        condition_raw=data["condition"],
        ambient_temperature=data["ambient_temperatur"],
        battery_level=data["battery_level"],
        overheating=data["overheating"],
        general_failure=data["general_failure"],
        last_change=data["last_change"],
    )


def group_values_from(raw: str) -> AggregateValues:
    """Build an aggregate state object from a recorded group payload.

    ``poll_group/<n>``, ``poll_broadcast`` and ``poll_ungrouped`` all carry the
    same fields, so this reads all three.
    """
    data = json.loads(raw)
    return AggregateValues(
        level=data["level"],
        last_level=data["last_level"],
        cct_level=data["cct_level"],
        vertical=data["vertical"],
        last_change=data["last_change"],
    )


def scene_values_from(raw: str) -> SceneValues:
    """Build a scene state object from a recorded ``poll_scene`` payload."""
    data = json.loads(raw)
    return SceneValues(
        active=bool(data["active"]),
        level=data["level"],
        last_change=data["last_change"],
    )


def make_setup_units(
    hass: HomeAssistant, make_entry: Callable[..., MockConfigEntry]
) -> Callable[..., Any]:
    """Build the helper behind the ``setup_units`` fixture.

    Test modules declare their own fixture over this function, which keeps the
    fixture out of the import list and pytest happy.
    """

    async def _setup(
        units: Iterable[UnitDefinition],
        *,
        prepare: Callable[[FakeGateway], None] | None = None,
        **entry_kwargs: Any,
    ) -> tuple[MockConfigEntry, FakeGateway]:
        created: list[FakeGateway] = []

        def _create(_hass: HomeAssistant, config: GatewayConfig) -> FakeGateway:
            gateway = FakeGateway(config)
            if prepare is not None:
                prepare(gateway)
            created.append(gateway)
            return gateway

        entry = make_entry(units=[unit.to_dict() for unit in units], **entry_kwargs)
        entry.add_to_hass(hass)
        with patch(
            "custom_components.casambi_lithernet.create_gateway", side_effect=_create
        ):
            await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()
        return entry, created[0]

    return _setup
