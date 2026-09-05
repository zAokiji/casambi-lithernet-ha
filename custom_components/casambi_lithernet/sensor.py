"""Diagnostic sensors derived from the units' properties.

**Placeholder created by package A, owned by package J from now on.**

Package J replaces this file completely. The stub sets up nothing so the
integration stays loadable while the packages are built in parallel.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import CasambiConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CasambiConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up no entities yet."""
