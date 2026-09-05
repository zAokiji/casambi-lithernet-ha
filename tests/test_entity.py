"""Package E: the shared entity base class.

The fake gateway defined here is also used by ``test_light.py``; it implements
the contract from ``contracts.py`` in memory, records every command and lets a
test push state as if the gateway had published it.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest
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
    TargetType,
    UnitKind,
)
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
    """Build an aggregate state object from a recorded group payload."""
    data = json.loads(raw)
    return AggregateValues(
        level=data["level"],
        last_level=data["last_level"],
        cct_level=data["cct_level"],
        vertical=data["vertical"],
        last_change=data["last_change"],
    )


def make_setup_units(
    hass: HomeAssistant, make_entry: Callable[..., MockConfigEntry]
) -> Callable[..., Any]:
    """Build the helper behind the ``setup_units`` fixture.

    ``test_light.py`` declares its own fixture over this function, which keeps
    the fixture out of the import list and pytest happy.
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
