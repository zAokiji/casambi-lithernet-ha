"""Fan platform for Casambi switching outputs used as a fan.

Owned by package F, together with :mod:`.switch`, which holds the shared
behaviour and the module docstring that explains the kind ``switch``.

This platform exists so a switching output can appear as a fan in the dashboard
and in voice control. It is the same element, the same command and the same
unique id as in the switch domain — only the domain differs, chosen by
:attr:`~.models.UnitDefinition.switch_domain`.

The fan supports on and off and nothing else: no percentage, no preset mode, no
oscillation and no direction, so the user interface offers no speed control.
``FanEntityFeature.TURN_ON | TURN_OFF`` is declared because Home Assistant
expects a fan to name those two explicitly.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import CasambiConfigEntry
from .const import LEVEL_MAX, LEVEL_MIN, SWITCH_DOMAIN_FAN
from .contracts import CasambiGateway
from .models import UnitDefinition
from .switch import CasambiSwitchingEntity, drop_stale_entity, wants_domain


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CasambiConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the fan entity of every element configured for this domain."""
    gateway = entry.runtime_data.gateway
    for subentry_id, definition in entry.runtime_data.units.items():
        if entities := build_fans(gateway, definition):
            async_add_entities(entities, config_subentry_id=subentry_id)
        else:
            drop_stale_entity(hass, gateway, definition, SWITCH_DOMAIN_FAN)


def build_fans(gateway: CasambiGateway, definition: UnitDefinition) -> list[CasambiFan]:
    """Build the fan entity this element asks for, if it asks for one."""
    if wants_domain(definition, SWITCH_DOMAIN_FAN):
        return [CasambiFan(gateway, definition)]
    return []


class CasambiFan(CasambiSwitchingEntity, FanEntity):
    """A Casambi switching output as a Home Assistant fan, on and off only."""

    _attr_supported_features = FanEntityFeature.TURN_ON | FanEntityFeature.TURN_OFF

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Switch on with level 255, ignoring any speed the caller passed."""
        await self.async_switch_to(LEVEL_MAX)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Switch off with level 0."""
        await self.async_switch_to(LEVEL_MIN)
