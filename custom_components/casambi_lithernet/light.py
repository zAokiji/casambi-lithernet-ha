"""Light platform for simple, tunable white, group and multi driver elements.

Owned by package E. The four kinds from project document section 6:

``simple``
    One ``target_level`` command, state from ``poll_device/<id>/values``.
``tunable_white``
    Brightness on ``target_level``, colour temperature on ``target_tc`` with
    the normalised value from :func:`~.state.kelvin_to_tc`. When Home Assistant
    sets both in one call the colour temperature goes first and the brightness
    second. The gateway never reports the colour temperature back, so it is
    kept optimistically while the brightness stays real.
``group``
    Like ``simple`` but addressed as a Casambi group, state from
    ``poll_group/<id>``.
``multi_dali``
    One entity per DALI driver on ``target_dimmers`` with the driver index
    starting at 0, always optimistic because the gateway only publishes a mixed
    value for the whole unit, plus an optional total entity on ``target_level``
    that may use that mixed value as its state.

The rule that matters most (project document 2.5): switching on with a
brightness must put **exactly one** command on the wire. A separate "on"
command with level 255 overwrites any dim or colour temperature command that
went before it, which is what the old YAML needed ``on_command_type:
brightness`` for. Every method below therefore sends the level command itself
instead of turning on first.
"""

from __future__ import annotations

from functools import partial
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_TRANSITION,
    LightEntity,
)
from homeassistant.components.light.const import ColorMode, LightEntityFeature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import CasambiConfigEntry
from .const import TargetType, UnitKind
from .contracts import CasambiGateway
from .entity import CasambiEntity
from .models import UnitDefinition
from .state import AggregateValues, UnitValues, kelvin_to_tc


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CasambiConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the light entities of every configured element."""
    gateway = entry.runtime_data.gateway
    for subentry_id, definition in entry.runtime_data.units.items():
        # Package K adds its scene and broadcast entities here from
        # light_scene_broadcast.py. Imported inside the function because that
        # module imports CasambiLight from here.
        from .light_scene_broadcast import build_scene_broadcast_lights

        if extra := build_scene_broadcast_lights(gateway, definition):
            async_add_entities(extra, config_subentry_id=subentry_id)
        if entities := build_lights(gateway, definition):
            async_add_entities(entities, config_subentry_id=subentry_id)


def build_lights(
    gateway: CasambiGateway, definition: UnitDefinition
) -> list[CasambiLight]:
    """Build every light entity one element definition asks for."""
    if definition.kind is UnitKind.SIMPLE:
        return [CasambiUnitLight(gateway, definition)]
    if definition.kind is UnitKind.TUNABLE_WHITE:
        return [CasambiTunableWhiteLight(gateway, definition)]
    if definition.kind is UnitKind.GROUP:
        return [CasambiGroupLight(gateway, definition)]
    if definition.kind is UnitKind.MULTI_DALI:
        entities: list[CasambiLight] = [
            CasambiDimmerLight(gateway, definition, index)
            for index in range(definition.dimmer_count)
        ]
        if definition.with_total_entity:
            entities.append(CasambiTotalLight(gateway, definition))
        return entities
    return []


class CasambiLight(CasambiEntity, LightEntity):
    """Shared behaviour of every Casambi light: on, off and brightness."""

    _attr_supported_features = LightEntityFeature.TRANSITION
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}  # noqa: RUF012
    _attr_is_on: bool | None = None
    _attr_brightness: int | None = None

    async def _async_send_level(self, level: int, duration_ms: int | None) -> None:
        """Send the one command that sets this light to ``level``."""
        raise NotImplementedError

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Switch on, with exactly one level command."""
        duration_ms = self._duration_ms(_transition(kwargs))
        level = self._resolve_on_level(_brightness(kwargs))
        await self._async_send_level(level, duration_ms)
        self._remember_level(level)
        self._after_command(partial(self._apply_level, level))

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Switch off by setting level 0."""
        duration_ms = self._duration_ms(_transition(kwargs))
        await self._async_send_level(0, duration_ms)
        self._after_command(partial(self._apply_level, 0))

    @callback
    def _apply_level(self, level: int) -> None:
        """Adopt a brightness, whether it was sent or reported."""
        self._attr_is_on = level > 0
        self._attr_brightness = level or None

    @callback
    def _handle_values(self, values: UnitValues | AggregateValues) -> None:
        """Adopt a state message from the gateway."""
        self._state_confirmed()
        self._apply_level(values.level)
        self.async_write_ha_state()


class CasambiUnitLight(CasambiLight):
    """A dimmable luminaire addressed as one Casambi unit (``simple``)."""

    def __init__(self, gateway: CasambiGateway, definition: UnitDefinition) -> None:
        """Name the entity after its device and use the element's unique id."""
        super().__init__(
            gateway,
            definition,
            unique_id=definition.base_unique_id(gateway.config.bridge_id),
        )

    async def _async_send_level(self, level: int, duration_ms: int | None) -> None:
        """Set the whole unit."""
        await self._gateway.async_set_level(
            TargetType.UNIT, self._definition.target_id, level, duration_ms
        )

    @callback
    def _read_initial_state(self) -> None:
        """Take the retained values the gateway already holds."""
        values = self._gateway.unit_values(self._definition.target_id)
        if values is not None:
            self._apply_level(values.level)

    @callback
    def _register_subscriptions(self) -> None:
        """Follow ``poll_device/<id>/values``."""
        self.async_on_remove(
            self._gateway.subscribe_unit(
                self._definition.target_id, self._handle_values
            )
        )


class CasambiTunableWhiteLight(CasambiUnitLight):
    """A unit with brightness and colour temperature.

    The colour temperature is optimistic: ``cct_level`` stays 0 in every
    message the reference installation produces, so the gateway can not confirm
    it and an incoming values message must not clear it.
    """

    _attr_color_mode = ColorMode.COLOR_TEMP
    _attr_supported_color_modes = {ColorMode.COLOR_TEMP}  # noqa: RUF012

    def __init__(self, gateway: CasambiGateway, definition: UnitDefinition) -> None:
        """Take the Kelvin limits from the element definition."""
        super().__init__(gateway, definition)
        self._attr_min_color_temp_kelvin = definition.min_kelvin
        self._attr_max_color_temp_kelvin = definition.max_kelvin

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Send the colour temperature first, then the brightness.

        A call that only changes the colour temperature of a light that is
        already on sends no level command at all, so the brightness the user
        set stays untouched.
        """
        duration_ms = self._duration_ms(_transition(kwargs))
        kelvin = _color_temp_kelvin(kwargs)
        brightness = _brightness(kwargs)

        if kelvin is not None:
            await self._gateway.async_set_tc(
                TargetType.UNIT,
                self._definition.target_id,
                kelvin_to_tc(
                    kelvin, self._definition.min_kelvin, self._definition.max_kelvin
                ),
                duration_ms,
            )
            self._attr_color_temp_kelvin = kelvin
            if brightness is None and self.is_on:
                self.async_write_ha_state()
                return

        level = self._resolve_on_level(brightness)
        await self._async_send_level(level, duration_ms)
        self._remember_level(level)
        self._after_command(partial(self._apply_level, level))


class CasambiGroupLight(CasambiLight):
    """A Casambi group, addressed with one command for all its members."""

    def __init__(self, gateway: CasambiGateway, definition: UnitDefinition) -> None:
        """Name the entity after its device and use the group's unique id."""
        super().__init__(
            gateway,
            definition,
            unique_id=definition.base_unique_id(gateway.config.bridge_id),
        )

    async def _async_send_level(self, level: int, duration_ms: int | None) -> None:
        """Set the whole group."""
        await self._gateway.async_set_level(
            TargetType.GROUP, self._definition.target_id, level, duration_ms
        )

    @callback
    def _read_initial_state(self) -> None:
        """Take the last group values the gateway holds, if any.

        Group topics are not retained, so this is often empty after a restart
        until the cyclic poll comes around.
        """
        values = self._gateway.group_values(self._definition.target_id)
        if values is not None:
            self._apply_level(values.level)

    @callback
    def _register_subscriptions(self) -> None:
        """Follow ``poll_group/<id>``."""
        self.async_on_remove(
            self._gateway.subscribe_group(
                self._definition.target_id, self._handle_values
            )
        )


class CasambiDimmerLight(CasambiLight):
    """One DALI driver inside a multi driver unit.

    Always optimistic: the gateway publishes a single mixed level for the whole
    unit and no per driver value at all, so this entity can never be confirmed.
    ``assumed_state`` makes Home Assistant show separate on and off buttons.
    """

    def __init__(
        self, gateway: CasambiGateway, definition: UnitDefinition, index: int
    ) -> None:
        """Bind the entity to one driver index, counting from 0."""
        super().__init__(
            gateway,
            definition,
            unique_id=definition.dimmer_unique_id(gateway.config.bridge_id, index),
            force_optimistic=True,
        )
        self._index = index
        self._use_own_name(definition.dimmer_name(index))

    async def _async_send_level(self, level: int, duration_ms: int | None) -> None:
        """Set just this driver."""
        await self._gateway.async_set_dimmer(
            self._definition.target_id, self._index, level, duration_ms
        )


class CasambiTotalLight(CasambiUnitLight):
    """All drivers of a multi driver unit at once.

    Uses ``target_level`` for the whole unit and may show the mixed level the
    gateway reports; that value says "something is on", not what one driver is
    doing.
    """

    def __init__(self, gateway: CasambiGateway, definition: UnitDefinition) -> None:
        """Name the entity through the ``total`` translation key."""
        super().__init__(gateway, definition)
        self._attr_translation_key = "total"


def _brightness(kwargs: dict[str, Any]) -> int | None:
    """Read the requested brightness out of a service call."""
    value = kwargs.get(ATTR_BRIGHTNESS)
    return int(value) if isinstance(value, (int, float)) else None


def _color_temp_kelvin(kwargs: dict[str, Any]) -> int | None:
    """Read the requested colour temperature out of a service call."""
    value = kwargs.get(ATTR_COLOR_TEMP_KELVIN)
    return int(value) if isinstance(value, (int, float)) else None


def _transition(kwargs: dict[str, Any]) -> float | None:
    """Read the requested transition time in seconds out of a service call."""
    value = kwargs.get(ATTR_TRANSITION)
    return float(value) if isinstance(value, (int, float)) else None
