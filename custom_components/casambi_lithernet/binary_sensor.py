"""Problem indicator derived from the units' condition byte.

Owned by package J. One binary sensor per Casambi element that the gateway
publishes ``propertys`` for, i.e. the unit addressed kinds; groups, scenes and
broadcast get nothing here because the gateway reports no condition for them.

The sensor is on whenever :attr:`~.state.UnitProperties.has_problem` is true,
which is every ``condition`` byte outside the healthy set (0x00, 0x80, 0xA0).
The matching plain text lives on the ``condition`` sensor of package J; this
entity exists so an automation or a dashboard can react to "something is wrong
with this luminaire" without knowing the code table.
"""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import CasambiConfigEntry
from .contracts import CasambiGateway
from .entity import CasambiEntity
from .models import UnitDefinition
from .state import UnitProperties

#: Unique id suffix and translation key of the problem sensor.
PROBLEM_KEY = "problem"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CasambiConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the problem sensor of every element that has properties."""
    gateway = entry.runtime_data.gateway
    for subentry_id, definition in entry.runtime_data.units.items():
        if entities := build_binary_sensors(gateway, definition):
            async_add_entities(entities, config_subentry_id=subentry_id)


def build_binary_sensors(
    gateway: CasambiGateway, definition: UnitDefinition
) -> list[CasambiProblemBinarySensor]:
    """Build the binary sensors one element definition asks for."""
    if not definition.has_properties:
        return []
    return [CasambiProblemBinarySensor(gateway, definition)]


class CasambiProblemBinarySensor(CasambiEntity, BinarySensorEntity):
    """Whether a unit reports anything other than a healthy condition."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_is_on: bool | None = None

    def __init__(self, gateway: CasambiGateway, definition: UnitDefinition) -> None:
        """Name the entity through the ``problem`` translation key."""
        super().__init__(
            gateway,
            definition,
            unique_id=definition.diagnostic_unique_id(
                gateway.config.bridge_id, PROBLEM_KEY
            ),
            translation_key=PROBLEM_KEY,
            read_only=True,
        )

    @callback
    def _apply_properties(self, properties: UnitProperties) -> None:
        """Adopt the condition byte on top of what the base class reads."""
        super()._apply_properties(properties)
        self._attr_is_on = properties.has_problem
