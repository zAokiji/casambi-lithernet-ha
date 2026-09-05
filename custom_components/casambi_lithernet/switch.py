"""Switch platform for Casambi switching outputs.

Belongs with :mod:`.fan`.

The kind ``switch`` (project document 6.5) is a Casambi unit with a plain
switching output: the WC fan of the reference installation. It knows on and
off, nothing else — no dimming, no speed steps, no run-on timer. A run-on
("keep running five minutes after the light goes out") belongs into a Home
Assistant automation, which is what the explanatory text in the subentry form
says as well.

One element produces **exactly one** entity. Which domain it lands in is stored
in :attr:`~.models.UnitDefinition.switch_domain`: ``switch`` builds it here,
``fan`` builds it in :mod:`.fan`. Both domains share
:class:`CasambiSwitchingEntity`, which lives in this module and is imported by
the fan platform, so on, off and the state reading exist only once.

On sends ``target_level`` with level 255, off sends level 0, and the state
comes from ``poll_device/<id>/values``: ``level > 0`` means on.
"""

from __future__ import annotations

from functools import partial
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import CasambiConfigEntry
from .const import (
    DOMAIN,
    LEVEL_MAX,
    LEVEL_MIN,
    SWITCH_DOMAIN_SWITCH,
    UnitKind,
)
from .contracts import CasambiGateway
from .entity import CasambiEntity
from .models import UnitDefinition
from .state import UnitValues


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CasambiConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the switch entity of every element configured for this domain."""
    gateway = entry.runtime_data.gateway
    for subentry_id, definition in entry.runtime_data.units.items():
        if entities := build_switches(gateway, definition):
            async_add_entities(entities, config_subentry_id=subentry_id)
        else:
            drop_stale_entity(hass, gateway, definition, SWITCH_DOMAIN_SWITCH)


def build_switches(
    gateway: CasambiGateway, definition: UnitDefinition
) -> list[CasambiSwitch]:
    """Build the switch entity this element asks for, if it asks for one."""
    if wants_domain(definition, SWITCH_DOMAIN_SWITCH):
        return [CasambiSwitch(gateway, definition)]
    return []


def wants_domain(definition: UnitDefinition, domain: str) -> bool:
    """Whether this element becomes an entity in ``domain``.

    Only the kind ``switch`` produces anything here, and only in the one domain
    its ``switch_domain`` names, so the two platforms never both build an
    entity for the same element.
    """
    return definition.kind is UnitKind.SWITCH and definition.switch_domain == domain


def drop_stale_entity(
    hass: HomeAssistant,
    gateway: CasambiGateway,
    definition: UnitDefinition,
    domain: str,
) -> None:
    """Remove the leftover registry entry after the domain was changed.

    Switching an element from ``switch`` to ``fan`` (or back) reloads the entry
    and builds the entity in the other domain. The registry entry of the old
    domain would otherwise stay behind as a restored, permanently unavailable
    entity, because nothing ever claims that unique id again.
    """
    if definition.kind is not UnitKind.SWITCH or definition.switch_domain == domain:
        return
    registry = er.async_get(hass)
    unique_id = definition.base_unique_id(gateway.config.bridge_id)
    if (
        entity_id := registry.async_get_entity_id(domain, DOMAIN, unique_id)
    ) is not None:
        registry.async_remove(entity_id)


class CasambiSwitchingEntity(CasambiEntity):
    """On and off for a Casambi unit with a plain switching output.

    Shared by the switch and the fan entity; the two subclasses add nothing but
    their Home Assistant platform base class and, for the fan, the declaration
    that it has no speed control.

    The unique id is :meth:`~.models.UnitDefinition.base_unique_id`, the same
    value in both domains: changing the domain moves the entity, it does not
    give it a new identity.
    """

    _attr_is_on: bool | None = None

    def __init__(self, gateway: CasambiGateway, definition: UnitDefinition) -> None:
        """Name the entity after its device and use the element's unique id."""
        super().__init__(
            gateway,
            definition,
            unique_id=definition.base_unique_id(gateway.config.bridge_id),
        )

    @property
    def is_on(self) -> bool | None:
        """Whether the output is switched on.

        Defined here rather than left to the platform base class because
        ``FanEntity`` derives ``is_on`` from the speed percentage, which this
        entity does not have.
        """
        return self._attr_is_on

    async def _async_send_level(self, level: int) -> None:
        """Send the one ``target_level`` command that switches the unit."""
        await self._gateway.async_set_level(
            self._definition.target_type, self._definition.target_id, level
        )

    async def async_switch_to(self, level: int) -> None:
        """Switch the output and take over the state afterwards."""
        await self._async_send_level(level)
        self._after_command(partial(self._apply_level, level), expect=level)

    @callback
    def _apply_level(self, level: int) -> None:
        """Adopt a level, whether it was sent or reported: > 0 means on."""
        self._attr_is_on = level > LEVEL_MIN

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

    @callback
    def _handle_values(self, values: UnitValues) -> None:
        """Adopt a state message from the gateway."""
        self._state_confirmed(values.level)
        self._apply_level(values.level)
        self.async_write_ha_state()


class CasambiSwitch(CasambiSwitchingEntity, SwitchEntity):
    """A Casambi switching output as a Home Assistant switch.

    The default for the kind, because the WC fan of the reference installation
    is a ``switch`` in the existing YAML and migrating it without renaming the
    entity means staying in that domain.
    """

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Switch on with level 255."""
        await self.async_switch_to(LEVEL_MAX)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Switch off with level 0."""
        await self.async_switch_to(LEVEL_MIN)
