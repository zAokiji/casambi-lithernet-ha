"""Diagnostic sensors derived from the units' properties.

Every sensor here reads one field of the retained
``poll_device/<id>/propertys`` message (project document 15.6), so all of them
are available right after a restart without asking the gateway for anything.

Six sensors per unit addressed element:

``condition`` and ``priority_source``
    Enumerations with plain text states. They are enabled by default because
    they answer the two questions the installation actually asks: is this
    luminaire healthy, and *who* set it last. The morning of 2026-09-04, when
    every luminaire was suddenly at one percent, could not be explained because
    nothing recorded the control source; ``priority_source`` is the fix.
``battery_level``, ``ambient_temperature``, ``overheating`` and
``general_failure``
    Raw values, disabled by default. The reference installation reports 0 for
    all four, so they would only add noise; a user with hardware that fills
    them in enables them in the entity settings.

The translation of a raw byte into a state string lives in
:class:`~.state.UnitProperties`, never here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from . import CasambiConfigEntry
from .const import (
    CONDITION_CODES,
    CONDITION_UNKNOWN,
    PRIORITY_AUTOMATION,
    PRIORITY_SOURCES,
    PRIORITY_UNKNOWN,
)
from .contracts import CasambiGateway
from .entity import CasambiEntity
from .models import UnitDefinition
from .state import UnitProperties


def _unique(values: list[str]) -> list[str]:
    """Keep the first occurrence of every value, order preserved."""
    return list(dict.fromkeys(values))


#: Every state ``UnitProperties.condition`` can produce. Several condition
#: bytes map to ``ok``, hence the deduplication.
CONDITION_STATES: list[str] = _unique([*CONDITION_CODES.values(), CONDITION_UNKNOWN])

#: Every state ``UnitProperties.priority_source`` can produce.
PRIORITY_STATES: list[str] = _unique(
    [*PRIORITY_SOURCES.values(), PRIORITY_AUTOMATION, PRIORITY_UNKNOWN]
)


@dataclass(frozen=True, kw_only=True)
class CasambiSensorDescription(SensorEntityDescription):
    """A diagnostic sensor plus the way it reads its value."""

    value_fn: Callable[[UnitProperties], StateType]


SENSOR_DESCRIPTIONS: tuple[CasambiSensorDescription, ...] = (
    CasambiSensorDescription(
        key="condition",
        translation_key="condition",
        device_class=SensorDeviceClass.ENUM,
        options=CONDITION_STATES,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda properties: properties.condition,
    ),
    CasambiSensorDescription(
        key="priority_source",
        translation_key="priority_source",
        device_class=SensorDeviceClass.ENUM,
        options=PRIORITY_STATES,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda properties: properties.priority_source,
    ),
    CasambiSensorDescription(
        key="battery_level",
        translation_key="battery_level",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda properties: properties.battery_level,
    ),
    CasambiSensorDescription(
        key="ambient_temperature",
        translation_key="ambient_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda properties: properties.ambient_temperature,
    ),
    CasambiSensorDescription(
        key="overheating",
        translation_key="overheating",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda properties: properties.overheating,
    ),
    CasambiSensorDescription(
        key="general_failure",
        translation_key="general_failure",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda properties: properties.general_failure,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CasambiConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the diagnostic sensors of every element that has properties."""
    gateway = entry.runtime_data.gateway
    for subentry_id, definition in entry.runtime_data.units.items():
        if entities := build_sensors(gateway, definition):
            async_add_entities(entities, config_subentry_id=subentry_id)


def build_sensors(
    gateway: CasambiGateway, definition: UnitDefinition
) -> list[CasambiDiagnosticSensor]:
    """Build the diagnostic sensors one element definition asks for."""
    if not definition.has_properties:
        return []
    return [
        CasambiDiagnosticSensor(gateway, definition, description)
        for description in SENSOR_DESCRIPTIONS
    ]


class CasambiDiagnosticSensor(CasambiEntity, SensorEntity):
    """One field of a unit's ``propertys`` message as a sensor."""

    entity_description: CasambiSensorDescription
    _attr_native_value: StateType = None

    def __init__(
        self,
        gateway: CasambiGateway,
        definition: UnitDefinition,
        description: CasambiSensorDescription,
    ) -> None:
        """Bind the entity to one property field."""
        super().__init__(
            gateway,
            definition,
            unique_id=definition.diagnostic_unique_id(
                gateway.config.bridge_id, description.key
            ),
            translation_key=description.translation_key,
            read_only=True,
        )
        self.entity_description = description

    @callback
    def _apply_properties(self, properties: UnitProperties) -> None:
        """Adopt this sensor's field on top of what the base class reads."""
        super()._apply_properties(properties)
        self._attr_native_value = self.entity_description.value_fn(properties)
