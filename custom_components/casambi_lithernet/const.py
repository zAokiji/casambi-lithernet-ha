"""Constants for the Casambi (Lithernet MQTT) integration.

This module is owned by package A. Every other package imports from here and
must not define topic strings, payload keys or magic numbers of its own.

All topic and payload names come from the Lithernet System Manual, chapter
5.6 ("MQTT"), cross-checked against a live capture of the gateway on
2026-09-05. Where manual and reality disagree, reality wins and the deviation
is noted in a comment.
"""

from __future__ import annotations

from enum import IntEnum, StrEnum
from typing import Final

DOMAIN: Final = "casambi_lithernet"

# Platforms the integration forwards config entries to.
PLATFORMS: Final[list[str]] = ["light", "switch", "fan", "binary_sensor", "sensor"]

# --------------------------------------------------------------------------
# Configuration keys (config entry data / options and subentry data)
# --------------------------------------------------------------------------

CONF_BRIDGE_ID: Final = "bridge_id"
CONF_TOPIC_PREFIX: Final = "topic_prefix"
CONF_GATEWAY_HOST: Final = "gateway_host"
CONF_POLLING_METHOD: Final = "polling_method"
CONF_DEFAULT_DURATION_MS: Final = "default_duration_ms"
CONF_DEFAULT_MIN_KELVIN: Final = "default_min_kelvin"
CONF_DEFAULT_MAX_KELVIN: Final = "default_max_kelvin"

CONF_KIND: Final = "kind"
CONF_TARGET_ID: Final = "target_id"
CONF_NAME: Final = "name"
CONF_MIN_KELVIN: Final = "min_kelvin"
CONF_MAX_KELVIN: Final = "max_kelvin"
CONF_DIMMER_COUNT: Final = "dimmer_count"
CONF_DIMMER_NAMES: Final = "dimmer_names"
CONF_WITH_TOTAL_ENTITY: Final = "with_total_entity"
CONF_DEFAULT_ON_LEVEL: Final = "default_on_level"
CONF_OPTIMISTIC_OVERRIDE: Final = "optimistic_override"
CONF_SWITCH_DOMAIN: Final = "switch_domain"

SUBENTRY_TYPE_UNIT: Final = "unit"

# --------------------------------------------------------------------------
# Defaults
# --------------------------------------------------------------------------

DEFAULT_BRIDGE_ID: Final = 0
DEFAULT_TOPIC_PREFIX: Final = "casambi"
# Deliberately empty: this is one household's address, not a sensible
# default for anybody else. The form shows a placeholder instead.
DEFAULT_GATEWAY_HOST: Final = ""
DEFAULT_DURATION_MS: Final = 0
DEFAULT_MIN_KELVIN: Final = 2700
DEFAULT_MAX_KELVIN: Final = 6500
DEFAULT_ON_LEVEL: Final = 255

# Casambi levels are 0-255 on the wire; Home Assistant brightness is 0-255 too,
# so no scaling is needed. Kept as names so intent stays readable.
LEVEL_MIN: Final = 0
LEVEL_MAX: Final = 255
TC_MIN: Final = 0
TC_MAX: Final = 255

# Highest dimmer index the gateway accepts (manual: dimmer_1..dimmer_4,
# element_* go to 8). Unit 16 in the reference installation uses four.
MAX_DIMMER_COUNT: Final = 8

# Valid Casambi address ranges (manual 5.13.2).
UNIT_ID_MIN: Final = 1
UNIT_ID_MAX: Final = 250
GROUP_ID_MIN: Final = 1
GROUP_ID_MAX: Final = 255
SCENE_ID_MIN: Final = 1
SCENE_ID_MAX: Final = 255

# How long a non-optimistic entity waits for the gateway to confirm a command
# before it adopts the value it sent (section 6.6 of the project document).
STATE_CONFIRM_TIMEOUT: Final = 3.0

# Seconds without any state message after which the "check polling method"
# repair is raised (section 8).
NO_STATE_REPAIR_AFTER: Final = 24 * 60 * 60

# --------------------------------------------------------------------------
# Topic building blocks
# --------------------------------------------------------------------------

TOPIC_SET: Final = "set"
TOPIC_GET: Final = "get"

# Commands (HA -> gateway), manual 5.6.2.2.
CMD_TARGET_LEVEL: Final = "target_level"
CMD_TARGET_TC: Final = "target_tc"
CMD_TARGET_DIMMERS: Final = "target_dimmers"
CMD_TARGET_ELEMENTS: Final = "target_elements"
CMD_TARGET_RGBW: Final = "target_rgbw"
CMD_TARGET_HUESAT: Final = "target_huesat"
CMD_TARGET_VERTICAL: Final = "target_vertical"
CMD_EXECUTE_AUTOMATION: Final = "execute_automation"
CMD_SCENE_LEVEL: Final = "scene_level"
CMD_GROUPS_LEVEL: Final = "groups_level"
CMD_BROADCAST_LEVEL: Final = "level"
CMD_LIGHT_SENSOR: Final = "light_sensor"
CMD_PIR_SENSOR: Final = "pir_sensor"
CMD_PUSH_BUTTON_PRESSED: Final = "push_button_pressed"
CMD_PUSH_BUTTON_RELEASED: Final = "push_button_released"
CMD_PUSH_BUTTON_LEVEL: Final = "push_button_level"

# State topics (gateway -> HA), manual 5.6.2.1.
GET_POLL_DEVICE: Final = "poll_device"
GET_POLL_GROUP: Final = "poll_group"
GET_POLL_SCENE: Final = "poll_scene"
GET_POLL_BROADCAST: Final = "poll_broadcast"
GET_POLL_UNGROUPED: Final = "poll_ungrouped"
GET_SCENE_CALL: Final = "scene_call"
# The gateway publishes this with a trailing slash; verified in the capture on
# 2026-09-05. Do not "fix" it.
GET_NODE_DELETED: Final = "node_deleted/"

# Sub-topics below poll_device/<id>.
SUB_VALUES: Final = "values"
SUB_PROPERTYS: Final = "propertys"  # spelling as published by the gateway

# Sub-topics below poll_devicet/<id>. Documented in the manual but never sent
# by the reference installation; they need Casambi Evolution >= 37.90 on the
# units. Package M is blocked until a capture proves they arrive.
GET_POLL_DEVICET: Final = "poll_devicet"
SUB_ELEMENT_DIMMER: Final = "element_dimmer"
SUB_ELEMENT_SLIDER: Final = "element_slider"
SUB_ELEMENT_ONOFF: Final = "element_onoff"
SUB_ELEMENT_BUTTON: Final = "element_button"
SUB_SENSORS: Final = "sensors"
GET_POLL_BUTTON: Final = "poll_button"

# --------------------------------------------------------------------------
# Payload keys
# --------------------------------------------------------------------------

KEY_LEVEL: Final = "level"
KEY_LAST_LEVEL: Final = "last_level"
KEY_CCT_LEVEL: Final = "cct_level"
KEY_SCENE: Final = "scene"
KEY_ACTIVE: Final = "active"
KEY_VERTICAL: Final = "vertical"
KEY_LAST_CHANGE: Final = "last_change"
KEY_DEVICE: Final = "device"

KEY_ONLINE: Final = "online"
KEY_NODE_TYPE: Final = "node_type"
KEY_PRIORITY: Final = "priority"
KEY_CONDITION: Final = "condition"
KEY_AMBIENT_TEMPERATURE: Final = "ambient_temperatur"  # gateway spelling
KEY_BATTERY_LEVEL: Final = "battery_level"
KEY_OVERHEATING: Final = "overheating"
KEY_GENERAL_FAILURE: Final = "general_failure"

KEY_DURATION: Final = "duration"
KEY_TARGET_ID: Final = "targetid"
KEY_TARGET_TYPE: Final = "targettype"
KEY_TC: Final = "tc"
KEY_DIMMER_INDEX: Final = "dimmer_index"
KEY_DIMMER_VALUE: Final = "dimmer_value"
KEY_GROUP: Final = "group"


class TargetType(IntEnum):
    """Casambi target types (manual 5.13.2)."""

    BROADCAST = 0
    UNIT = 1
    GROUP = 2  # target id 0 addresses the ungrouped luminaires
    SCENE_ACTIVE = 3  # scene, only the lights currently on
    SCENE_ALL = 4  # scene, all lights
    MANUFACTURER = 5


UNGROUPED_TARGET_ID: Final = 0
BROADCAST_TARGET_ID: Final = 0


class PollingMethod(StrEnum):
    """Polling methods offered by the gateway (manual 4.3.2).

    The numeric suffixes name the *Casambi Evolution firmware of the units*,
    not the gateway firmware.
    """

    INACTIVE = "inactive"
    ACTIVE = "active"
    PASSIVE = "passive"
    PASSIVE_37_80 = "passive_37_80"
    PASSIVE_37_90 = "passive_37_90"
    PASSIVE_39_52 = "passive_39_52"


#: Polling methods that deliver device state at all.
POLLING_WITH_STATE: Final = frozenset(
    {
        PollingMethod.ACTIVE,
        PollingMethod.PASSIVE,
        PollingMethod.PASSIVE_37_80,
        PollingMethod.PASSIVE_37_90,
        PollingMethod.PASSIVE_39_52,
    }
)

DEFAULT_POLLING_METHOD: Final = PollingMethod.PASSIVE_37_80


class UnitKind(StrEnum):
    """Kinds of Casambi elements the integration can represent.

    The first block is implemented in version 0.1. The reserved block exists so
    stored configuration stays forward compatible; those values are never
    offered in the config flow and have no platform code.
    """

    SIMPLE = "simple"
    TUNABLE_WHITE = "tunable_white"
    MULTI_DALI = "multi_dali"
    GROUP = "group"
    SWITCH = "switch"
    SCENE = "scene"
    BROADCAST = "broadcast"

    # Reserved, no behaviour yet. See project document section 15.
    RGBW = "rgbw"
    VERTICAL = "vertical"
    SENSOR_UNIT = "sensor_unit"
    BUTTON_UNIT = "button_unit"
    VIRTUAL_INPUT = "virtual_input"


#: Kinds a user can actually create in version 0.1.
IMPLEMENTED_KINDS: Final = (
    UnitKind.SIMPLE,
    UnitKind.TUNABLE_WHITE,
    UnitKind.MULTI_DALI,
    UnitKind.GROUP,
    UnitKind.SWITCH,
    UnitKind.SCENE,
    UnitKind.BROADCAST,
)

#: Kinds that address a single Casambi unit and therefore have `propertys`
#: (online, condition, priority) and diagnostic entities.
UNIT_ADDRESSED_KINDS: Final = (
    UnitKind.SIMPLE,
    UnitKind.TUNABLE_WHITE,
    UnitKind.MULTI_DALI,
    UnitKind.SWITCH,
)

#: Kinds that do not carry a target id of their own.
TARGETLESS_KINDS: Final = (UnitKind.BROADCAST,)

SWITCH_DOMAIN_SWITCH: Final = "switch"
SWITCH_DOMAIN_FAN: Final = "fan"

# --------------------------------------------------------------------------
# Diagnostic value tables (manual 5.10.2, "Casambi_Data" description)
# --------------------------------------------------------------------------

#: `condition` byte -> short machine readable state.
CONDITION_CODES: Final[dict[int, str]] = {
    0x00: "ok",
    0x80: "ok",
    0xA0: "ok",
    0x01: "overheated",
    0x09: "overload",
    0x81: "thermal_overload",
    0x82: "lamp_failure",
    0x83: "driver_failure",
    0x85: "incompatible_hw",
    0x86: "hw_not_found",
    0x87: "configuration_failed",
}

CONDITION_OK: Final = "ok"
CONDITION_UNKNOWN: Final = "unrecognized"

#: `priority` low bits -> what last set the luminaire.
PRIORITY_SOURCES: Final[dict[int, str]] = {
    0: "none",
    1: "emergency",
    2: "bms_override",
    3: "manual_control",
    8: "presence",
    11: "date_timer",
    12: "clock_timer",
    15: "startup",
}

PRIORITY_AUTOMATION: Final = "automation"
PRIORITY_UNKNOWN: Final = "unrecognized"

#: Button states (manual 5.6.2.1.7). Used by package M.
BUTTON_STATE_SHORT_PRESS: Final = 2
BUTTON_STATE_LONG_PRESS_START: Final = 9
BUTTON_STATE_LONG_PRESS_STOP: Final = 12
