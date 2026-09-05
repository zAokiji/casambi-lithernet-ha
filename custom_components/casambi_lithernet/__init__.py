"""The Casambi (Lithernet MQTT) integration.

Owned by package A. Sets up one gateway bridge per config entry, keeps the
parsed configuration on the entry's runtime data and forwards to the platforms.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN, PLATFORMS, SUBENTRY_TYPE_UNIT
from .gateway import create_gateway
from .models import ConfigurationError, GatewayConfig, RuntimeData, UnitDefinition
from .repairs import async_check_mqtt, async_clear_issues, async_watch_for_state

_LOGGER = logging.getLogger(__name__)

type CasambiConfigEntry = ConfigEntry[RuntimeData]

MANUFACTURER = "Lithernet"
MODEL = "Casambi Gateway"


async def async_setup_entry(hass: HomeAssistant, entry: CasambiConfigEntry) -> bool:
    """Set up one gateway bridge."""
    if not await async_check_mqtt(hass, entry):
        raise ConfigEntryNotReady("MQTT integration is not available")

    config = GatewayConfig.from_dict({**entry.data, **entry.options})
    gateway = create_gateway(hass, config)
    await gateway.async_start()
    entry.async_on_unload(async_watch_for_state(hass, entry, gateway))

    entry.runtime_data = RuntimeData(
        config=config,
        gateway=gateway,
        units=_read_units(entry),
    )

    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, config.device_unique_id)},
        manufacturer=MANUFACTURER,
        model=MODEL,
        name=entry.title,
        configuration_url=(
            f"http://{config.gateway_host}" if config.gateway_host else None
        ),
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    _LOGGER.info(
        "Casambi bridge %s set up with %d elements",
        config.bridge_id,
        len(entry.runtime_data.units),
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CasambiConfigEntry) -> bool:
    """Tear the bridge down again."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.gateway.async_stop()
    return unloaded


async def _async_reload_entry(hass: HomeAssistant, entry: CasambiConfigEntry) -> None:
    """Reload when options or subentries changed."""
    await hass.config_entries.async_reload(entry.entry_id)


def _read_units(entry: CasambiConfigEntry) -> dict[str, UnitDefinition]:
    """Turn the stored subentries into definitions, skipping broken ones."""
    units: dict[str, UnitDefinition] = {}
    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type != SUBENTRY_TYPE_UNIT:
            continue
        try:
            units[subentry_id] = UnitDefinition.from_dict(dict(subentry.data))
        except ConfigurationError:
            _LOGGER.exception(
                "Skipping element %r because its configuration is unusable",
                subentry.title,
            )
    return units


async def async_remove_entry(hass: HomeAssistant, entry: CasambiConfigEntry) -> None:
    """Clean up after the user removed the bridge.

    Both repair issues are keyed by the entry id, so they have to go with it.
    """
    async_clear_issues(hass, entry)
