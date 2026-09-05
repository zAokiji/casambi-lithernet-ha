"""Package C tests: the guided gateway setup and the options flow."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.casambi_lithernet import config_flow
from custom_components.casambi_lithernet.const import (
    CONF_BRIDGE_ID,
    CONF_DEFAULT_DURATION_MS,
    CONF_DEFAULT_MAX_KELVIN,
    CONF_DEFAULT_MIN_KELVIN,
    CONF_GATEWAY_HOST,
    CONF_POLLING_METHOD,
    CONF_TOPIC_PREFIX,
    DEFAULT_TOPIC_PREFIX,
    DOMAIN,
    PollingMethod,
)
from custom_components.casambi_lithernet.contracts import CaptureResult

COMPONENT_DIR = Path(__file__).parent.parent / "custom_components" / "casambi_lithernet"

FULL_CAPTURE = CaptureResult(
    seconds=20.0,
    message_count=560,
    unit_ids=(2, 4, 12),
    group_ids=(1, 2),
    scene_ids=(1,),
    topic_kinds=("poll_device/<id>/propertys", "poll_device/<id>/values"),
)

EMPTY_CAPTURE = CaptureResult(seconds=20.0, message_count=0)


class FakeGateway:
    """Stands in for the MQTT gateway during the checks."""

    def __init__(self, log: dict[str, Any]) -> None:
        """Record everything into the shared log dictionary."""
        self.log = log
        log.setdefault("started", 0)
        log.setdefault("stopped", 0)
        log.setdefault("blinks", [])
        log.setdefault("captures", [])

    async def async_start(self) -> None:
        """Count one start."""
        self.log["started"] += 1

    async def async_stop(self) -> None:
        """Count one stop; must match the starts in every exit."""
        self.log["stopped"] += 1

    async def async_blink_test(self, unit_id: int, seconds: float = 2.0) -> None:
        """Note the blinked unit, optionally failing like a dead gateway."""
        self.log["blinks"].append(unit_id)
        if self.log.get("blink_raises"):
            raise RuntimeError("gateway did not answer")

    async def async_capture(self, seconds: float) -> CaptureResult:
        """Return the prepared capture result, or raise it."""
        self.log["captures"].append(seconds)
        result = self.log.get("capture_result", FULL_CAPTURE)
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture
def gateway_log(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace the real gateway of the checks and record what it was asked."""
    log: dict[str, Any] = {}

    def _create(hass: HomeAssistant, config: Any) -> FakeGateway:
        log["config"] = config
        return FakeGateway(log)

    monkeypatch.setattr(config_flow, "create_gateway", _create)
    monkeypatch.setattr(config_flow, "CAPTURE_SECONDS", 0.01)
    monkeypatch.setattr(config_flow, "BLINK_SECONDS", 0.0)
    return log


async def _start(hass: HomeAssistant) -> dict[str, Any]:
    """Open the config flow at the prerequisites step."""
    return await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})


async def _configure(
    hass: HomeAssistant, flow_id: str, user_input: dict[str, Any] | None = None
) -> dict[str, Any]:
    return await hass.config_entries.flow.async_configure(flow_id, user_input or {})


async def _walk_to_blink(
    hass: HomeAssistant,
    bridge_id: int = 0,
    unit_id: int = 19,
    polling: PollingMethod = PollingMethod.PASSIVE_37_80,
) -> dict[str, Any]:
    """Walk steps 1 to 5a and stop at the blink menu."""
    result = await _start(hass)
    assert result["step_id"] == "user"
    result = await _configure(hass, result["flow_id"])
    assert result["step_id"] == "gateway"
    result = await _configure(
        hass,
        result["flow_id"],
        {CONF_GATEWAY_HOST: "192.0.2.10", CONF_BRIDGE_ID: bridge_id},
    )
    assert result["step_id"] == "polling"
    result = await _configure(
        hass,
        result["flow_id"],
        {CONF_POLLING_METHOD: str(polling)},
    )
    assert result["step_id"] == "hints"
    result = await _configure(hass, result["flow_id"])
    assert result["step_id"] == "verify_command"
    return await _configure(
        hass, result["flow_id"], {config_flow.CONF_TEST_UNIT_ID: unit_id}
    )


# ------------------------------------------------------- the happy path --


async def test_full_flow_creates_the_entry(
    hass: HomeAssistant, mqtt_mock: MagicMock, gateway_log: dict[str, Any]
) -> None:
    """All six steps produce an entry with the expected data."""
    result = await _walk_to_blink(hass, bridge_id=3, unit_id=19)
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "verify_blink"
    assert gateway_log["blinks"] == [19]

    result = await _configure(hass, result["flow_id"], {"next_step_id": "blink_yes"})
    assert result["step_id"] == "verify_state"
    result = await _configure(hass, result["flow_id"])
    assert result["step_id"] == "verify_state_result"
    placeholders = result["description_placeholders"]
    assert placeholders["count"] == "560"
    assert placeholders["topics"] == ", ".join(FULL_CAPTURE.topic_kinds)

    result = await _configure(hass, result["flow_id"], {"next_step_id": "finish"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        CONF_BRIDGE_ID: 3,
        CONF_TOPIC_PREFIX: DEFAULT_TOPIC_PREFIX,
        CONF_GATEWAY_HOST: "192.0.2.10",
        CONF_POLLING_METHOD: str(PollingMethod.PASSIVE_37_80),
        CONF_DEFAULT_DURATION_MS: 0,
        CONF_DEFAULT_MIN_KELVIN: 2700,
        CONF_DEFAULT_MAX_KELVIN: 6500,
    }
    assert "3" in result["title"]
    assert gateway_log["started"] == gateway_log["stopped"] == 2


async def test_prerequisites_show_the_broker(
    hass: HomeAssistant, mqtt_mock: MagicMock, gateway_log: dict[str, Any]
) -> None:
    """Broker address and port reach the texts of the first steps."""
    result = await _start(hass)
    placeholders = result["description_placeholders"]
    assert set(placeholders) == {"broker", "port", "broker_note"}
    result = await _configure(hass, result["flow_id"])
    assert set(result["description_placeholders"]) == {"broker", "port", "broker_note"}


@pytest.mark.parametrize(
    ("broker", "expect_note"),
    [
        ("core-mosquitto", True),
        ("mosquitto", True),
        ("192.0.2.20", False),
        ("broker.example.org", False),
    ],
)
def test_broker_note_warns_about_names_only_home_assistant_resolves(
    broker: str, expect_note: bool
) -> None:
    """An add-on hostname is a dead end for a device on the network.

    The Casambi gateway has to reach the broker itself. Repeating a name like
    ``core-mosquitto`` at it would look right and never connect, which is
    exactly the failure the blink test then reports without an explanation.
    """
    note = config_flow.broker_note(broker)
    assert bool(note) is expect_note
    if expect_note:
        assert "IP-Adresse" in note


# ------------------------------------------------------------- aborting --


async def test_abort_without_mqtt(hass: HomeAssistant) -> None:
    """Without the MQTT integration there is nothing to talk through."""
    result = await _start(hass)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "mqtt_missing"


async def test_second_bridge_with_same_id_is_refused(
    hass: HomeAssistant, mqtt_mock: MagicMock, gateway_log: dict[str, Any]
) -> None:
    """A bridge is identified by its ID; the second entry for it is refused."""
    MockConfigEntry(
        domain=DOMAIN, unique_id=f"{DOMAIN}_0", data={CONF_BRIDGE_ID: 0}
    ).add_to_hass(hass)

    result = await _start(hass)
    result = await _configure(hass, result["flow_id"])
    result = await _configure(
        hass,
        result["flow_id"],
        {CONF_GATEWAY_HOST: "192.0.2.10", CONF_BRIDGE_ID: 0},
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_bridge_id_out_of_range_shows_the_error(
    hass: HomeAssistant, mqtt_mock: MagicMock, gateway_log: dict[str, Any]
) -> None:
    """An impossible bridge ID keeps the user in the form."""
    result = await _start(hass)
    result = await _configure(hass, result["flow_id"])
    result = await _configure(
        hass,
        result["flow_id"],
        {CONF_GATEWAY_HOST: "192.0.2.10", CONF_BRIDGE_ID: 999},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_BRIDGE_ID: "bridge_id_invalid"}


# --------------------------------------------------------- blink test ----


async def test_blink_repeat_blinks_again(
    hass: HomeAssistant, mqtt_mock: MagicMock, gateway_log: dict[str, Any]
) -> None:
    """Repeating sends the same unit through the test once more."""
    result = await _walk_to_blink(hass, unit_id=19)
    result = await _configure(hass, result["flow_id"], {"next_step_id": "blink_repeat"})
    assert result["step_id"] == "verify_blink"
    assert gateway_log["blinks"] == [19, 19]
    assert gateway_log["started"] == gateway_log["stopped"] == 2


async def test_blink_no_shows_the_help_text(
    hass: HomeAssistant, mqtt_mock: MagicMock, gateway_log: dict[str, Any]
) -> None:
    """Saying no leads to the three checks and both ways out."""
    result = await _walk_to_blink(hass, bridge_id=2)
    result = await _configure(hass, result["flow_id"], {"next_step_id": "blink_no"})
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "blink_no"
    assert result["menu_options"] == ["verify_command", "verify_state"]
    assert result["description_placeholders"]["bridge_id"] == "2"

    retry = await _configure(
        hass, result["flow_id"], {"next_step_id": "verify_command"}
    )
    assert retry["step_id"] == "verify_command"


async def test_failing_blink_leads_to_the_help_text(
    hass: HomeAssistant, mqtt_mock: MagicMock, gateway_log: dict[str, Any]
) -> None:
    """A gateway that raises must not end the setup."""
    gateway_log["blink_raises"] = True
    result = await _walk_to_blink(hass)
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "blink_no"
    assert gateway_log["started"] == gateway_log["stopped"] == 1


# --------------------------------------------------------- state check ---


async def test_state_check_without_messages(
    hass: HomeAssistant, mqtt_mock: MagicMock, gateway_log: dict[str, Any]
) -> None:
    """Zero messages show the hint about the polling method."""
    gateway_log["capture_result"] = EMPTY_CAPTURE
    result = await _walk_to_blink(hass)
    result = await _configure(hass, result["flow_id"], {"next_step_id": "blink_yes"})
    result = await _configure(hass, result["flow_id"])
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "no_state"
    assert result["menu_options"] == ["verify_state", "finish"]

    result = await _configure(hass, result["flow_id"], {"next_step_id": "finish"})
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_failing_capture_is_treated_as_no_state(
    hass: HomeAssistant, mqtt_mock: MagicMock, gateway_log: dict[str, Any]
) -> None:
    """A capture that raises still stops its gateway and reports nothing."""
    gateway_log["capture_result"] = RuntimeError("no broker")
    result = await _walk_to_blink(hass)
    result = await _configure(hass, result["flow_id"], {"next_step_id": "blink_yes"})
    result = await _configure(hass, result["flow_id"])
    assert result["step_id"] == "no_state"
    assert gateway_log["started"] == gateway_log["stopped"] == 2


async def test_capture_gateway_is_stopped_when_the_flow_is_abandoned(
    hass: HomeAssistant, mqtt_mock: MagicMock, gateway_log: dict[str, Any]
) -> None:
    """Aborting the flow leaves no gateway running."""
    result = await _walk_to_blink(hass)
    hass.config_entries.flow.async_abort(result["flow_id"])
    await hass.async_block_till_done()
    assert gateway_log["started"] == gateway_log["stopped"] == 1


async def test_inactive_polling_skips_the_state_check(
    hass: HomeAssistant, mqtt_mock: MagicMock, gateway_log: dict[str, Any]
) -> None:
    """With "inactive" there is nothing to listen for, so nothing is captured."""
    result = await _walk_to_blink(hass, polling=PollingMethod.INACTIVE)
    result = await _configure(hass, result["flow_id"], {"next_step_id": "blink_yes"})
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "polling_inactive"
    assert result["menu_options"] == ["finish", "polling"]
    assert gateway_log["captures"] == []

    result = await _configure(hass, result["flow_id"], {"next_step_id": "finish"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_POLLING_METHOD] == str(PollingMethod.INACTIVE)
    assert gateway_log["captures"] == []


async def test_inactive_hint_leads_back_to_the_polling_step(
    hass: HomeAssistant, mqtt_mock: MagicMock, gateway_log: dict[str, Any]
) -> None:
    """The hint offers to pick a passive method after all."""
    result = await _walk_to_blink(hass, polling=PollingMethod.INACTIVE)
    result = await _configure(hass, result["flow_id"], {"next_step_id": "blink_yes"})
    result = await _configure(hass, result["flow_id"], {"next_step_id": "polling"})
    assert result["step_id"] == "polling"

    result = await _configure(
        hass,
        result["flow_id"],
        {CONF_POLLING_METHOD: str(PollingMethod.PASSIVE_37_80)},
    )
    assert result["step_id"] == "hints"


async def test_inactive_polling_also_skips_the_check_after_a_failed_blink(
    hass: HomeAssistant, mqtt_mock: MagicMock, gateway_log: dict[str, Any]
) -> None:
    """Skipping from the blink help must not start a capture either."""
    result = await _walk_to_blink(hass, polling=PollingMethod.INACTIVE)
    result = await _configure(hass, result["flow_id"], {"next_step_id": "blink_no"})
    result = await _configure(hass, result["flow_id"], {"next_step_id": "verify_state"})
    assert result["step_id"] == "polling_inactive"
    assert gateway_log["captures"] == []


def test_capture_placeholders_carry_every_value() -> None:
    """Each value of the summary is its own placeholder, none is a word."""
    placeholders = config_flow.capture_placeholders(FULL_CAPTURE)
    assert placeholders == {
        "count": "560",
        "seconds": "20",
        "units": "2, 4, 12",
        "groups": "1, 2",
        "scenes": "1",
        "topics": "poll_device/<id>/propertys, poll_device/<id>/values",
        "devicet": config_flow.NOTHING,
        "buttons": config_flow.NOTHING,
    }


def test_capture_placeholders_use_a_dash_for_what_was_not_seen() -> None:
    """Missing addresses and kinds stay a dash, a seen kind gets a tick."""
    placeholders = config_flow.capture_placeholders(
        CaptureResult(
            seconds=20.0,
            message_count=1,
            topic_kinds=("poll_devicet/<id>/element_dimmer", "poll_button/<id>"),
        )
    )
    assert placeholders["units"] == config_flow.NOTHING
    assert placeholders["groups"] == config_flow.NOTHING
    assert placeholders["scenes"] == config_flow.NOTHING
    assert placeholders["devicet"] == config_flow.SEEN
    assert placeholders["buttons"] == config_flow.SEEN


def test_summary_texts_contain_no_untranslated_labels() -> None:
    """The German result text carries the labels, the values carry no words."""
    de = json.loads(
        (COMPONENT_DIR / "translations/de.json").read_text(encoding="utf-8")
    )
    result = de["config"]["step"]["verify_state_result"]["description"]
    assert "Gruppen:" in result
    assert "Szenen:" in result
    assert "Groups" not in result
    assert "Scenes" not in result
    assert "messages" not in result
    assert de["options"]["step"]["verify_result"]["description"] == result

    placeholders = config_flow.capture_placeholders(FULL_CAPTURE)
    for name in placeholders:
        assert f"{{{name}}}" in result
    # Only the topic kinds contain letters, and those are technical names the
    # gateway produces, not words to translate.
    for name, value in placeholders.items():
        if name == "topics":
            continue
        assert not any(char.isalpha() for char in value), name


# -------------------------------------------------------- options flow ---


async def test_options_change_the_defaults(
    hass: HomeAssistant,
    mqtt_mock: MagicMock,
    gateway_log: dict[str, Any],
    casambi_entry: Any,
) -> None:
    """The defaults menu writes the five values into the entry options."""
    result = await hass.config_entries.options.async_init(casambi_entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["settings", "verify"]

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "settings"}
    )
    assert result["step_id"] == "settings"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_POLLING_METHOD: str(PollingMethod.ACTIVE),
            CONF_DEFAULT_DURATION_MS: 500,
            CONF_DEFAULT_MIN_KELVIN: 2200,
            CONF_DEFAULT_MAX_KELVIN: 6000,
            CONF_GATEWAY_HOST: "192.0.2.11",
        },
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert casambi_entry.options[CONF_POLLING_METHOD] == str(PollingMethod.ACTIVE)
    assert casambi_entry.options[CONF_DEFAULT_DURATION_MS] == 500
    assert casambi_entry.options[CONF_DEFAULT_MIN_KELVIN] == 2200
    assert casambi_entry.options[CONF_DEFAULT_MAX_KELVIN] == 6000
    assert casambi_entry.options[CONF_GATEWAY_HOST] == "192.0.2.11"


async def test_options_verify_runs_the_same_check(
    hass: HomeAssistant,
    mqtt_mock: MagicMock,
    gateway_log: dict[str, Any],
    casambi_entry: Any,
) -> None:
    """The connection check captures and shows the same summary."""
    result = await hass.config_entries.options.async_init(casambi_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "verify"}
    )
    assert result["step_id"] == "verify"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["step_id"] == "verify_result"
    assert result["description_placeholders"] == config_flow.capture_placeholders(
        FULL_CAPTURE
    )
    assert gateway_log["started"] == gateway_log["stopped"] == 1

    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY


# -------------------------------------------------------- translations ---


def _flat_keys(obj: Any, prefix: str = "") -> set[str]:
    if isinstance(obj, dict):
        keys: set[str] = set()
        for key, value in obj.items():
            keys |= _flat_keys(value, f"{prefix}/{key}")
        return keys
    return {prefix}


def test_translation_files_stay_in_lockstep() -> None:
    """strings.json, en.json and de.json must describe the same keys."""
    files = {
        name: json.loads((COMPONENT_DIR / path).read_text(encoding="utf-8"))
        for name, path in (
            ("strings", "strings.json"),
            ("en", "translations/en.json"),
            ("de", "translations/de.json"),
        )
    }
    key_sets = {name: _flat_keys(data) for name, data in files.items()}
    assert key_sets["strings"] == key_sets["en"]
    assert key_sets["strings"] == key_sets["de"]


def test_every_flow_step_has_translations() -> None:
    """Every step the flows can show exists in the translations."""
    strings = json.loads((COMPONENT_DIR / "strings.json").read_text(encoding="utf-8"))
    config_steps = strings["config"]["step"]
    assert set(config_steps) >= {
        "user",
        "gateway",
        "polling",
        "hints",
        "verify_command",
        "verify_blink",
        "blink_no",
        "verify_state",
        "verify_state_result",
        "no_state",
        "polling_inactive",
    }
    assert set(strings["config"]["abort"]) >= {"mqtt_missing", "already_configured"}
    assert set(strings["options"]["step"]) >= {
        "init",
        "settings",
        "verify",
        "verify_result",
    }
    options = set(strings["selector"]["polling_method"]["options"])
    assert options == {str(method) for method in PollingMethod}
