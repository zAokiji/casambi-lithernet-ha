"""Shared test fixtures.

Owned by package A. Packages B to G build their tests on these helpers; do not
hand-roll MQTT mocking in individual test modules.

The three things a test usually needs:

* ``casambi_entry`` — a config entry for bridge 0, optionally with elements.
* ``publish_log`` — every payload the integration sent, already decoded.
* ``feed`` — inject a gateway message and let Home Assistant process it.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Generator, Iterable
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_mqtt_message,
)

from custom_components.casambi_lithernet.const import (
    CONF_BRIDGE_ID,
    CONF_GATEWAY_HOST,
    CONF_POLLING_METHOD,
    CONF_TOPIC_PREFIX,
    DEFAULT_BRIDGE_ID,
    DEFAULT_GATEWAY_HOST,
    DEFAULT_TOPIC_PREFIX,
    DOMAIN,
    SUBENTRY_TYPE_UNIT,
    PollingMethod,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: None,
) -> Generator[None]:
    """Load the integration from ``custom_components`` in every test."""
    yield


@pytest.fixture
def payload() -> Callable[[str], str]:
    """Read a recorded gateway payload from ``tests/fixtures``.

    The files hold real messages captured from the installation, so a parser
    test fails when the gateway's actual format changes.
    """

    def _read(name: str) -> str:
        return (FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8").strip()

    return _read


@pytest.fixture
def make_entry() -> Callable[..., MockConfigEntry]:
    """Build a config entry for one bridge, with optional elements.

    Pass elements as dictionaries in the shape
    :meth:`~custom_components.casambi_lithernet.models.UnitDefinition.to_dict`
    produces.
    """

    def _make(
        *,
        bridge_id: int = DEFAULT_BRIDGE_ID,
        polling_method: PollingMethod = PollingMethod.PASSIVE_37_80,
        units: Iterable[dict[str, Any]] = (),
        options: dict[str, Any] | None = None,
    ) -> MockConfigEntry:
        subentries = tuple(
            {
                "subentry_type": SUBENTRY_TYPE_UNIT,
                "data": dict(unit),
                "title": str(unit.get("name", "Element")),
                "unique_id": None,
            }
            for unit in units
        )
        return MockConfigEntry(
            domain=DOMAIN,
            title=f"Casambi Bridge {bridge_id}",
            unique_id=f"{DOMAIN}_{bridge_id}",
            data={
                CONF_BRIDGE_ID: bridge_id,
                CONF_TOPIC_PREFIX: DEFAULT_TOPIC_PREFIX,
                CONF_GATEWAY_HOST: DEFAULT_GATEWAY_HOST,
                CONF_POLLING_METHOD: str(polling_method),
            },
            options=options or {},
            subentries_data=subentries,
        )

    return _make


@pytest.fixture
async def casambi_entry(
    hass: HomeAssistant,
    mqtt_mock: MagicMock,
    make_entry: Callable[..., MockConfigEntry],
) -> MockConfigEntry:
    """Provide a set up config entry for bridge 0 with no elements."""
    entry = make_entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


@pytest.fixture
def publish_log(mqtt_mock: MagicMock) -> Callable[[], list[tuple[str, dict[str, Any]]]]:
    """Return every message the integration published, newest last.

    Payloads are decoded from JSON so assertions compare dictionaries rather
    than strings, which keeps them independent of key order.
    """

    def _log() -> list[tuple[str, dict[str, Any]]]:
        messages: list[tuple[str, dict[str, Any]]] = []
        for call in mqtt_mock.async_publish.call_args_list:
            topic = call.args[0]
            raw = call.args[1]
            try:
                decoded = json.loads(raw)
            except (TypeError, ValueError):
                decoded = {"raw": raw}
            messages.append((topic, decoded))
        return messages

    return _log


@pytest.fixture
def feed(hass: HomeAssistant) -> Callable[[str, str | dict[str, Any]], Any]:
    """Inject a message as if the gateway had published it.

    Accepts a dictionary for convenience; it is serialised the way the gateway
    would. Returns the awaitable that settles Home Assistant.
    """

    async def _feed(topic: str, message: str | dict[str, Any]) -> None:
        raw = message if isinstance(message, str) else json.dumps(message)
        async_fire_mqtt_message(hass, topic, raw)
        await hass.async_block_till_done()

    return _feed
