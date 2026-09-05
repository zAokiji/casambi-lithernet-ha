"""Diagnostics download for one gateway entry.

Owned by package G. Serves the standard "Download diagnostics" button of Home
Assistant with what section 4.2 of the project document asks for: the gateway
configuration, the configured elements, and the counters the gateway keeps
(commands sent, messages received and discarded, last message per topic with a
timestamp, subscribed topics).

**Nothing is redacted, because there is nothing to redact.** The integration
owns no credentials: it publishes and subscribes through the Home Assistant
MQTT integration, which holds the broker login, and the gateway's web interface
is never contacted. The only address in here is the gateway host on the local
network, which the user needs in order to read the file. ``test_diagnostics.py``
asserts that no credential ever sneaks in.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.core import HomeAssistant

from . import CasambiConfigEntry
from .models import UnitDefinition


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: CasambiConfigEntry
) -> dict[str, Any]:
    """Collect everything that helps to understand a bridge."""
    runtime = entry.runtime_data
    config = runtime.config
    diagnostics = runtime.gateway.diagnostics()

    return {
        "entry": {
            "title": entry.title,
            "version": entry.version,
            "source": entry.source,
            "element_count": len(runtime.units),
        },
        "config": {
            **config.to_dict(),
            "delivers_state": config.delivers_state,
            "device_unique_id": config.device_unique_id,
        },
        "elements": [
            _element(subentry_id, definition, config.bridge_id)
            for subentry_id, definition in runtime.units.items()
        ],
        "gateway": {
            "available": runtime.gateway.available,
            "commands_sent": diagnostics.commands_sent,
            "messages_received": diagnostics.messages_received,
            "invalid_messages": diagnostics.invalid_messages,
            "subscribed_topics": list(diagnostics.subscribed_topics),
            "last_message_per_topic": dict(diagnostics.last_message_per_topic),
        },
    }


def _element(
    subentry_id: str, definition: UnitDefinition, bridge_id: int
) -> dict[str, Any]:
    """Describe one configured element, including the ids it produces."""
    element = asdict(definition)
    element["kind"] = str(definition.kind)
    element["dimmer_names"] = list(definition.dimmer_names)
    element["subentry_id"] = subentry_id
    element["unique_id"] = definition.base_unique_id(bridge_id)
    element["target_type"] = int(definition.target_type)
    element["has_properties"] = definition.has_properties
    return element
