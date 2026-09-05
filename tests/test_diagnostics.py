"""Package G: the "Download diagnostics" export.

These tests run against the real gateway of package B, not a fake, so the
counters and the last message per topic are the ones a user would really get.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.casambi_lithernet.const import PollingMethod, UnitKind
from custom_components.casambi_lithernet.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.casambi_lithernet.models import UnitDefinition

UNIT = UnitDefinition(kind=UnitKind.SIMPLE, name="Kochnische", target_id=12)
GROUP = UnitDefinition(kind=UnitKind.GROUP, name="Kueche indirekt", target_id=2)

VALUES_TOPIC = "casambi/0/get/poll_device/12/values"

#: Nothing that looks like a credential may ever appear in the export.
FORBIDDEN = (
    "password",
    "passwd",
    "username",
    "user_name",
    "token",
    "secret",
    "api_key",
    "apikey",
    "credential",
    "auth",
    "certificate",
)


@pytest.fixture
async def bridge(hass: HomeAssistant, mqtt_mock, make_entry) -> MockConfigEntry:
    """Set up a bridge with two elements and the real gateway object."""
    entry = make_entry(units=[UNIT.to_dict(), GROUP.to_dict()])
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def walk(value: Any) -> list[str]:
    """Flatten every key and string of the export for the secrets check."""
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            found.append(str(key))
            found.extend(walk(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(walk(item))
    else:
        found.append(str(value))
    return found


async def test_export_describes_the_gateway(
    hass: HomeAssistant, bridge: MockConfigEntry
) -> None:
    """The configuration of the bridge is in the export."""
    data = await async_get_config_entry_diagnostics(hass, bridge)
    assert data["config"]["bridge_id"] == 0
    assert data["config"]["topic_prefix"] == "casambi"
    assert data["config"]["polling_method"] == str(PollingMethod.PASSIVE_37_80)
    assert data["config"]["delivers_state"] is True
    assert data["entry"]["element_count"] == 2


async def test_export_lists_every_element(
    hass: HomeAssistant, bridge: MockConfigEntry
) -> None:
    """Both configured elements appear with their address and unique id."""
    data = await async_get_config_entry_diagnostics(hass, bridge)
    by_name = {element["name"]: element for element in data["elements"]}
    assert set(by_name) == {"Kochnische", "Kueche indirekt"}

    unit = by_name["Kochnische"]
    assert unit["kind"] == str(UnitKind.SIMPLE)
    assert unit["target_id"] == 12
    assert unit["unique_id"] == "casambi_lithernet_0_u12"
    assert unit["has_properties"] is True
    assert unit["subentry_id"] in bridge.subentries

    group = by_name["Kueche indirekt"]
    assert group["unique_id"] == "casambi_lithernet_0_g2"
    assert group["has_properties"] is False


async def test_export_carries_the_gateway_counters(
    hass: HomeAssistant, bridge: MockConfigEntry, feed
) -> None:
    """Commands sent, messages received and discarded are all counted."""
    await feed(VALUES_TOPIC, {"level": 42, "last_level": 42})
    await feed(VALUES_TOPIC, "not json at all")
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": "light.kochnische"}, blocking=True
    )

    data = await async_get_config_entry_diagnostics(hass, bridge)
    assert data["gateway"]["commands_sent"] == 1
    assert data["gateway"]["messages_received"] == 2
    assert data["gateway"]["invalid_messages"] == 1
    assert data["gateway"]["available"] is True


async def test_export_shows_the_last_message_per_topic(
    hass: HomeAssistant, bridge: MockConfigEntry, feed
) -> None:
    """The newest payload of every topic is in the export with a timestamp."""
    await feed(VALUES_TOPIC, {"level": 7, "last_level": 7})
    data = await async_get_config_entry_diagnostics(hass, bridge)

    assert VALUES_TOPIC in data["gateway"]["subscribed_topics"]
    last = data["gateway"]["last_message_per_topic"][VALUES_TOPIC]
    assert json.loads(last["payload"])["level"] == 7
    assert last["received"]
    assert "retained" in last


async def test_export_is_json_serialisable(
    hass: HomeAssistant, bridge: MockConfigEntry, feed
) -> None:
    """Home Assistant writes the export as JSON, so it must survive that."""
    await feed(VALUES_TOPIC, {"level": 7, "last_level": 7})
    data = await async_get_config_entry_diagnostics(hass, bridge)
    assert json.loads(json.dumps(data)) == data


async def test_export_contains_no_credentials(
    hass: HomeAssistant, bridge: MockConfigEntry, feed
) -> None:
    """The integration owns no secrets, and none turn up in the download.

    The broker login lives in the MQTT integration and the gateway's web
    interface is never contacted, so there is nothing to redact here. This test
    is the guard that keeps it that way.
    """
    await feed(VALUES_TOPIC, {"level": 7, "last_level": 7})
    data = await async_get_config_entry_diagnostics(hass, bridge)

    for text in walk(data):
        lowered = text.lower()
        assert not any(word in lowered for word in FORBIDDEN), text
    assert "**REDACTED**" not in json.dumps(data)
