"""MQTT gateway implementation.

**Placeholder created by package A, owned by package B from now on.**

Package B replaces this file completely with the real implementation of
:class:`~.contracts.CasambiGateway`. The stub exists so the integration loads
and the other packages can be developed in parallel; it accepts commands and
drops them, and reports no state.

Package B must keep the module level function :func:`create_gateway` with this
exact signature, because ``__init__.py`` calls it.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from .const import TargetType
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
from .state import AggregateValues, SceneValues, UnitProperties, UnitValues


def create_gateway(hass: HomeAssistant, config: GatewayConfig) -> CasambiGateway:
    """Build the gateway object for one bridge."""
    return _StubGateway(hass, config)


def _noop() -> None:
    """Unsubscribe callback that does nothing."""


class _StubGateway(CasambiGateway):
    """Does nothing, so the integration stays loadable until package B lands."""

    def __init__(self, hass: HomeAssistant, config: GatewayConfig) -> None:
        """Remember what the real implementation will need."""
        self.hass = hass
        self.config = config

    async def async_start(self) -> None:
        """Nothing to attach to yet."""

    async def async_stop(self) -> None:
        """Nothing to detach."""

    @property
    def available(self) -> bool:
        """The stub never reports a usable connection."""
        return False

    def subscribe_availability(self, callback: AvailabilityCallback) -> Unsubscribe:
        """Ignore the watcher."""
        return _noop

    async def async_set_level(
        self,
        target_type: TargetType,
        target_id: int,
        level: int,
        duration_ms: int | None = None,
    ) -> None:
        """Drop the command."""

    async def async_set_tc(
        self,
        target_type: TargetType,
        target_id: int,
        tc: int,
        duration_ms: int | None = None,
    ) -> None:
        """Drop the command."""

    async def async_set_dimmer(
        self,
        target_id: int,
        dimmer_index: int,
        value: int,
        duration_ms: int | None = None,
    ) -> None:
        """Drop the command."""

    async def async_set_scene_level(
        self, scene_id: int, level: int, duration_ms: int | None = None
    ) -> None:
        """Drop the command."""

    async def async_set_broadcast_level(
        self, level: int, duration_ms: int | None = None
    ) -> None:
        """Drop the command."""

    def subscribe_unit(self, unit_id: int, callback: UnitValuesCallback) -> Unsubscribe:
        """Ignore the subscription."""
        return _noop

    def subscribe_unit_properties(
        self, unit_id: int, callback: UnitPropertiesCallback
    ) -> Unsubscribe:
        """Ignore the subscription."""
        return _noop

    def subscribe_group(
        self, group_id: int, callback: AggregateCallback
    ) -> Unsubscribe:
        """Ignore the subscription."""
        return _noop

    def subscribe_scene(self, scene_id: int, callback: SceneCallback) -> Unsubscribe:
        """Ignore the subscription."""
        return _noop

    def subscribe_broadcast(self, callback: AggregateCallback) -> Unsubscribe:
        """Ignore the subscription."""
        return _noop

    def unit_values(self, unit_id: int) -> UnitValues | None:
        """No state known."""
        return None

    def unit_properties(self, unit_id: int) -> UnitProperties | None:
        """No state known."""
        return None

    def group_values(self, group_id: int) -> AggregateValues | None:
        """No state known."""
        return None

    def scene_values(self, scene_id: int) -> SceneValues | None:
        """No state known."""
        return None

    def broadcast_values(self) -> AggregateValues | None:
        """No state known."""
        return None

    async def async_blink_test(self, unit_id: int, seconds: float = 2.0) -> None:
        """Do nothing visible."""

    async def async_capture(self, seconds: float) -> CaptureResult:
        """Report an empty capture."""
        return CaptureResult(seconds=seconds, message_count=0)

    def diagnostics(self) -> GatewayDiagnostics:
        """Report empty diagnostics."""
        return GatewayDiagnostics()
