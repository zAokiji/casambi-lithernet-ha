"""Package G: the two repair issues from section 8 of the project document.

The fake gateway comes from ``test_entity.py`` ; this module only
adds a message counter on top, because that is the single thing the "no state
at all" watchdog looks at.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.repairs import ConfirmRepairFlow
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from test_entity import FakeGateway

from custom_components.casambi_lithernet.const import (
    DOMAIN,
    NO_STATE_REPAIR_AFTER,
    PollingMethod,
)
from custom_components.casambi_lithernet.contracts import GatewayDiagnostics
from custom_components.casambi_lithernet.models import GatewayConfig
from custom_components.casambi_lithernet.repairs import (
    ISSUE_MQTT_MISSING,
    ISSUE_NO_STATE_RECEIVED,
    STATE_CHECK_INTERVAL,
    async_check_mqtt,
    async_create_fix_flow,
    async_watch_for_state,
    build_issue_id,
)

TRANSLATION_FILES = (
    "strings.json",
    "translations/de.json",
    "translations/en.json",
)


class CountingGateway(FakeGateway):
    """Fake gateway whose diagnostics a test can move by hand."""

    def __init__(self, config: GatewayConfig) -> None:
        """Start out listening to one topic and having received nothing."""
        super().__init__(config)
        self.received = 0
        self.topics: tuple[str, ...] = ("casambi/0/get/poll_device/12/values",)

    def diagnostics(self) -> GatewayDiagnostics:
        """Report only what the watchdog reads."""
        return GatewayDiagnostics(
            messages_received=self.received, subscribed_topics=self.topics
        )


def issue_of(hass: HomeAssistant, entry: MockConfigEntry, kind: str):
    """Look up one of our issues, or None when it is not raised."""
    return ir.async_get(hass).async_get_issue(DOMAIN, build_issue_id(kind, entry))


@pytest.fixture
def entry(hass: HomeAssistant, make_entry) -> MockConfigEntry:
    """Provide an entry that is known to Home Assistant but not set up."""
    created = make_entry()
    created.add_to_hass(hass)
    return created


def watch(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    polling_method: PollingMethod = PollingMethod.PASSIVE_37_80,
) -> tuple[CountingGateway, object]:
    """Start the watchdog over a silent gateway with the given polling."""
    gateway = CountingGateway(GatewayConfig(polling_method=polling_method))
    cancel = async_watch_for_state(hass, entry, gateway)
    return gateway, cancel


async def tick(hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: float):
    """Let the given time pass and run everything that was due."""
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


# ------------------------------------------------------------ MQTT missing --


async def test_mqtt_missing_raises_the_issue(
    hass: HomeAssistant, mqtt_mock, entry: MockConfigEntry
) -> None:
    """Without the MQTT integration the user is told why nothing loads."""
    with patch(
        "custom_components.casambi_lithernet.repairs.mqtt.async_wait_for_mqtt_client",
        return_value=False,
    ):
        assert await async_check_mqtt(hass, entry) is False
    issue = issue_of(hass, entry, ISSUE_MQTT_MISSING)
    assert issue is not None
    assert issue.translation_key == ISSUE_MQTT_MISSING
    assert issue.severity is ir.IssueSeverity.ERROR
    assert issue.is_fixable is False


async def test_mqtt_issue_disappears_when_mqtt_returns(
    hass: HomeAssistant, mqtt_mock, entry: MockConfigEntry
) -> None:
    """The next successful setup withdraws the issue again."""
    with patch(
        "custom_components.casambi_lithernet.repairs.mqtt.async_wait_for_mqtt_client",
        return_value=False,
    ):
        await async_check_mqtt(hass, entry)
    assert issue_of(hass, entry, ISSUE_MQTT_MISSING) is not None

    assert await async_check_mqtt(hass, entry) is True
    assert issue_of(hass, entry, ISSUE_MQTT_MISSING) is None


# -------------------------------------------------------- no state at all --


async def test_no_state_issue_appears_after_a_day(
    hass: HomeAssistant, mqtt_mock, entry: MockConfigEntry, freezer
) -> None:
    """A day of complete silence raises the "check the polling" repair."""
    _, cancel = watch(hass, entry)
    await tick(hass, freezer, NO_STATE_REPAIR_AFTER + 60)

    issue = issue_of(hass, entry, ISSUE_NO_STATE_RECEIVED)
    assert issue is not None
    assert issue.translation_key == ISSUE_NO_STATE_RECEIVED
    assert issue.severity is ir.IssueSeverity.WARNING
    cancel()


async def test_no_state_issue_stays_away_before_the_day_is_over(
    hass: HomeAssistant, mqtt_mock, entry: MockConfigEntry, freezer
) -> None:
    """Half a day of silence is not enough to bother the user."""
    _, cancel = watch(hass, entry)
    await tick(hass, freezer, NO_STATE_REPAIR_AFTER / 2)
    assert issue_of(hass, entry, ISSUE_NO_STATE_RECEIVED) is None
    cancel()


async def test_no_state_issue_disappears_when_state_arrives(
    hass: HomeAssistant, mqtt_mock, entry: MockConfigEntry, freezer
) -> None:
    """The first message from the gateway withdraws the repair."""
    gateway, cancel = watch(hass, entry)
    await tick(hass, freezer, NO_STATE_REPAIR_AFTER + 60)
    assert issue_of(hass, entry, ISSUE_NO_STATE_RECEIVED) is not None

    gateway.received = 1
    await tick(hass, freezer, STATE_CHECK_INTERVAL.total_seconds() + 1)
    assert issue_of(hass, entry, ISSUE_NO_STATE_RECEIVED) is None
    cancel()


async def test_a_message_before_the_deadline_prevents_the_issue(
    hass: HomeAssistant, mqtt_mock, entry: MockConfigEntry, freezer
) -> None:
    """A gateway that reports is never questioned, however long it runs."""
    gateway, cancel = watch(hass, entry)
    gateway.received = 3
    await tick(hass, freezer, NO_STATE_REPAIR_AFTER * 2)
    assert issue_of(hass, entry, ISSUE_NO_STATE_RECEIVED) is None
    cancel()


async def test_a_bridge_without_elements_never_complains(
    hass: HomeAssistant, mqtt_mock, entry: MockConfigEntry, freezer
) -> None:
    """Without an element nothing is subscribed, so silence proves nothing."""
    gateway, cancel = watch(hass, entry)
    gateway.topics = ()
    await tick(hass, freezer, NO_STATE_REPAIR_AFTER * 2)
    assert issue_of(hass, entry, ISSUE_NO_STATE_RECEIVED) is None
    cancel()


async def test_inactive_polling_never_complains(
    hass: HomeAssistant, mqtt_mock, entry: MockConfigEntry, freezer
) -> None:
    """With polling ``inactive`` silence is what the user asked for."""
    _, cancel = watch(hass, entry, PollingMethod.INACTIVE)
    await tick(hass, freezer, NO_STATE_REPAIR_AFTER * 2)
    assert issue_of(hass, entry, ISSUE_NO_STATE_RECEIVED) is None
    cancel()


async def test_unloading_withdraws_the_issue(
    hass: HomeAssistant, mqtt_mock, entry: MockConfigEntry, freezer
) -> None:
    """Stopping the watchdog takes the repair off the dashboard."""
    _, cancel = watch(hass, entry)
    await tick(hass, freezer, NO_STATE_REPAIR_AFTER + 60)
    assert issue_of(hass, entry, ISSUE_NO_STATE_RECEIVED) is not None

    cancel()
    assert issue_of(hass, entry, ISSUE_NO_STATE_RECEIVED) is None
    # Nothing is checked any more, so nothing comes back either.
    await tick(hass, freezer, NO_STATE_REPAIR_AFTER)
    assert issue_of(hass, entry, ISSUE_NO_STATE_RECEIVED) is None


async def test_two_bridges_raise_their_own_issue(
    hass: HomeAssistant, mqtt_mock, make_entry, freezer
) -> None:
    """Issue ids carry the entry id, so bridges do not overwrite each other."""
    first = make_entry(bridge_id=0)
    second = make_entry(bridge_id=1)
    first.add_to_hass(hass)
    second.add_to_hass(hass)

    _, cancel = watch(hass, first)
    await tick(hass, freezer, NO_STATE_REPAIR_AFTER + 60)
    assert issue_of(hass, first, ISSUE_NO_STATE_RECEIVED) is not None
    assert issue_of(hass, second, ISSUE_NO_STATE_RECEIVED) is None
    cancel()


# ------------------------------------------------------------ translations --


@pytest.mark.parametrize("filename", TRANSLATION_FILES)
@pytest.mark.parametrize("key", [ISSUE_MQTT_MISSING, ISSUE_NO_STATE_RECEIVED])
def test_every_issue_has_a_text(filename: str, key: str) -> None:
    """Both issues are translated in all three files, title and description."""
    path = Path(__file__).parents[1] / "custom_components" / DOMAIN / filename
    issues = json.loads(path.read_text(encoding="utf-8"))["issues"]
    assert issues[key]["title"]
    assert issues[key]["description"]


async def test_the_repairs_platform_is_complete(hass: HomeAssistant) -> None:
    """Home Assistant refuses a repairs platform without a fix flow factory."""
    flow = await async_create_fix_flow(hass, "mqtt_missing_x", None)
    assert isinstance(flow, ConfirmRepairFlow)
