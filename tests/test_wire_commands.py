"""What actually reaches the wire, for every kind that sends commands.

Like ``test_regressions.py`` and ``test_end_to_end.py``
this module runs the real
:class:`~custom_components.casambi_lithernet.gateway.MqttCasambiGateway` with a
mocked broker underneath, so an assertion here is about the bytes an
installation would see, not about a test double.

Three things are checked:

1. **Exactly one message when switching on, for every kind.** Project document
   2.5: a second command with level 255 overwrites the brightness or colour
   temperature that went before it. ``test_regressions.py`` covers five kinds;
   this module covers all nine entity flavours that can send a command,
   including the total entity of a multi driver unit, which is where the
   mistake actually happened in the reference installation. The whole published
   list is compared, so an extra message fails the test wherever it appears.
2. **Every call shape of a tunable white light**, once on a luminaire that is
   off and once on one that is on. The interesting one is "colour temperature
   only, luminaire off": it has to send the colour temperature *and* a level,
   because the level command is the one that switches the luminaire on. On a
   luminaire that is already on the same call must send no level at all.
3. **The last known state survives an unusable message.** Rubbish on a topic
   is discarded; the good value stays readable and an entity added afterwards
   renders it.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from datetime import timedelta
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import async_get_platforms
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_mqtt_message,
    async_fire_time_changed,
)

from custom_components.casambi_lithernet.const import (
    DOMAIN,
    STATE_CONFIRM_TIMEOUT,
    SWITCH_DOMAIN_FAN,
    UnitKind,
)
from custom_components.casambi_lithernet.models import UnitDefinition
from custom_components.casambi_lithernet.switch import CasambiSwitch

BASE = "casambi/0"
LEVEL_TOPIC = f"{BASE}/set/target_level"
TC_TOPIC = f"{BASE}/set/target_tc"
DIMMER_TOPIC = f"{BASE}/set/target_dimmers"
SCENE_TOPIC = f"{BASE}/set/scene_level"
BROADCAST_TOPIC = f"{BASE}/set/level"
VALUES_TOPIC = f"{BASE}/get/poll_device/20/values"


async def setup_bridge(
    hass: HomeAssistant,
    make_entry: Callable[..., MockConfigEntry],
    units: Iterable[UnitDefinition],
    **entry_kwargs: Any,
) -> MockConfigEntry:
    """Set up one bridge with the real gateway and the given elements."""
    entry = make_entry(units=[unit.to_dict() for unit in units], **entry_kwargs)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def entity_id_of(hass: HomeAssistant, platform: str, unique_id: str) -> str:
    """Look an entity up by its unique id rather than by its name."""
    entity_id = er.async_get(hass).async_get_entity_id(platform, DOMAIN, unique_id)
    assert entity_id is not None, unique_id
    return entity_id


async def let_the_fallback_run(hass: HomeAssistant) -> None:
    """Move past the three second confirmation window of section 6.6.

    A non optimistic entity adopts the value it sent once the gateway stayed
    quiet for :data:`~.const.STATE_CONFIRM_TIMEOUT`, so this is what makes the
    resulting entity state observable without inventing a gateway answer.
    """
    async_fire_time_changed(
        hass, dt_util.utcnow() + timedelta(seconds=STATE_CONFIRM_TIMEOUT + 1)
    )
    await hass.async_block_till_done()


# ------------------- 2.5: exactly one message, for every kind that sends --

#: One case per entity flavour that can send a command. ``service`` and
#: ``data`` describe the call, ``expected`` is the complete list of messages
#: that call is allowed to produce.
ONE_MESSAGE_CASES: tuple[tuple[str, UnitDefinition, str, str, dict, list], ...] = (
    (
        "simple",
        UnitDefinition(kind=UnitKind.SIMPLE, name="Kochnische", target_id=12),
        "light",
        f"{DOMAIN}_0_u12",
        {"brightness": 37},
        [(LEVEL_TOPIC, {"level": 37, "duration": 0, "targetid": 12, "targettype": 1})],
    ),
    (
        "tunable_white",
        UnitDefinition(kind=UnitKind.TUNABLE_WHITE, name="Badspot 1", target_id=20),
        "light",
        f"{DOMAIN}_0_u20",
        {"brightness": 37},
        [(LEVEL_TOPIC, {"level": 37, "duration": 0, "targetid": 20, "targettype": 1})],
    ),
    (
        "group",
        UnitDefinition(kind=UnitKind.GROUP, name="Kueche indirekt", target_id=2),
        "light",
        f"{DOMAIN}_0_g2",
        {"brightness": 37},
        [(LEVEL_TOPIC, {"level": 37, "duration": 0, "targetid": 2, "targettype": 2})],
    ),
    (
        "multi_dali_driver",
        UnitDefinition(
            kind=UnitKind.MULTI_DALI,
            name="Gang",
            target_id=16,
            dimmer_count=2,
            dimmer_names=("Gang Spot 1", "Gang Spot 2"),
            with_total_entity=False,
        ),
        "light",
        f"{DOMAIN}_0_u16_d0",
        {"brightness": 37},
        [
            (
                DIMMER_TOPIC,
                {
                    "dimmer_index": 0,
                    "dimmer_value": 37,
                    "duration": 0,
                    "targetid": 16,
                    "targettype": 1,
                },
            )
        ],
    ),
    (
        # The one the mutation slipped through on: the total entity of a multi
        # driver unit is the element that was wrong in the real installation.
        "multi_dali_total",
        UnitDefinition(
            kind=UnitKind.MULTI_DALI,
            name="Wohnzimmer",
            target_id=15,
            dimmer_count=3,
            dimmer_names=("Linear direkt", "Indirekt 1", "Indirekt 2"),
            with_total_entity=True,
        ),
        "light",
        f"{DOMAIN}_0_u15",
        {"brightness": 37},
        [(LEVEL_TOPIC, {"level": 37, "duration": 0, "targetid": 15, "targettype": 1})],
    ),
    (
        "switch",
        UnitDefinition(kind=UnitKind.SWITCH, name="WC Luefter", target_id=4),
        "switch",
        f"{DOMAIN}_0_u4",
        {},
        [(LEVEL_TOPIC, {"level": 255, "duration": 0, "targetid": 4, "targettype": 1})],
    ),
    (
        "fan",
        UnitDefinition(
            kind=UnitKind.SWITCH,
            name="WC Luefter",
            target_id=4,
            switch_domain=SWITCH_DOMAIN_FAN,
        ),
        "fan",
        f"{DOMAIN}_0_u4",
        {},
        [(LEVEL_TOPIC, {"level": 255, "duration": 0, "targetid": 4, "targettype": 1})],
    ),
    (
        "scene",
        UnitDefinition(kind=UnitKind.SCENE, name="Abendlicht", target_id=3),
        "light",
        f"{DOMAIN}_0_s3",
        {"brightness": 37},
        [(SCENE_TOPIC, {"scene": 3, "level": 37, "duration": 0})],
    ),
    (
        "broadcast",
        UnitDefinition(kind=UnitKind.BROADCAST, name="Alle Leuchten"),
        "light",
        f"{DOMAIN}_0_broadcast",
        {"brightness": 37},
        [(BROADCAST_TOPIC, {"level": 37, "duration": 0})],
    ),
)


@pytest.mark.parametrize(
    ("unit", "platform", "unique_id", "data", "expected"),
    [case[1:] for case in ONE_MESSAGE_CASES],
    ids=[case[0] for case in ONE_MESSAGE_CASES],
)
async def test_switching_on_publishes_exactly_one_message(
    hass: HomeAssistant,
    mqtt_mock,
    make_entry,
    publish_log,
    unit: UnitDefinition,
    platform: str,
    unique_id: str,
    data: dict[str, Any],
    expected: list[tuple[str, dict[str, Any]]],
) -> None:
    """Switching on puts one message on the wire, whatever the kind.

    The comparison covers the whole published list, not just its first entry,
    so an additional command fails this test no matter where it is sent from:
    a separate "on" with level 255 would overwrite the brightness that came
    with it (docs/DESIGN.md, "Der wichtigste Fallstrick").
    """
    await setup_bridge(hass, make_entry, [unit])
    entity_id = entity_id_of(hass, platform, unique_id)

    await hass.services.async_call(
        platform, "turn_on", {"entity_id": entity_id, **data}, blocking=True
    )

    assert publish_log() == expected


# ---------------------------------- 6.2: the four call shapes of tunable --

TUNABLE = UnitDefinition(
    kind=UnitKind.TUNABLE_WHITE,
    name="Badspot 1",
    target_id=20,
    min_kelvin=2700,
    max_kelvin=6500,
    default_on_level=200,
)

#: What the luminaire last reported while it was off: level 0, and a level of
#: 96 to go back to. 96 is neither the brightness a case asks for, nor 255, nor
#: ``default_on_level``, so every source of the level is told apart.
OFF_LAST_LEVEL = 96

_TC_4600 = {"tc": 128, "duration": 0, "targetid": 20, "targettype": 1}


def _level(level: int) -> tuple[str, dict[str, Any]]:
    """Build the level message the tunable white light of this module sends."""
    return (
        LEVEL_TOPIC,
        {"level": level, "duration": 0, "targetid": 20, "targettype": 1},
    )


#: ``(id, lit, call, expected messages, expected brightness afterwards)``.
TUNABLE_CASES: tuple[tuple[str, bool, dict, list, int], ...] = (
    ("brightness_only_off", False, {"brightness": 90}, [_level(90)], 90),
    (
        # The path nothing covered before: a colour temperature alone has to
        # switch the luminaire on, which takes a level command as well.
        "colour_only_off",
        False,
        {"color_temp_kelvin": 4600},
        [(TC_TOPIC, _TC_4600), _level(OFF_LAST_LEVEL)],
        OFF_LAST_LEVEL,
    ),
    (
        "both_off",
        False,
        {"brightness": 90, "color_temp_kelvin": 4600},
        [(TC_TOPIC, _TC_4600), _level(90)],
        90,
    ),
    ("neither_off", False, {}, [_level(OFF_LAST_LEVEL)], OFF_LAST_LEVEL),
    ("brightness_only_on", True, {"brightness": 90}, [_level(90)], 90),
    (
        # Already on: the brightness the user set stays untouched, so there is
        # no level command at all.
        "colour_only_on",
        True,
        {"color_temp_kelvin": 4600},
        [(TC_TOPIC, _TC_4600)],
        4,
    ),
    (
        "both_on",
        True,
        {"brightness": 90, "color_temp_kelvin": 4600},
        [(TC_TOPIC, _TC_4600), _level(90)],
        90,
    ),
    ("neither_on", True, {}, [_level(4)], 4),
)


@pytest.mark.parametrize(
    ("lit", "data", "expected", "brightness"),
    [case[1:] for case in TUNABLE_CASES],
    ids=[case[0] for case in TUNABLE_CASES],
)
async def test_tunable_white_call_shapes(
    hass: HomeAssistant,
    mqtt_mock,
    make_entry,
    publish_log,
    payload,
    lit: bool,
    data: dict[str, Any],
    expected: list[tuple[str, dict[str, Any]]],
    brightness: int,
) -> None:
    """Every way Home Assistant calls ``turn_on`` on a tunable white light.

    Whatever the shape of the call, the luminaire is on afterwards and its
    brightness is the one the messages asked for. The colour temperature is
    never reported back by the gateway, so it is kept optimistically.
    """
    await setup_bridge(hass, make_entry, [TUNABLE])

    if lit:
        state_message = payload("unit_values_on")
    else:
        off = json.loads(payload("unit_values_off"))
        state_message = json.dumps({**off, "last_level": OFF_LAST_LEVEL})
    async_fire_mqtt_message(hass, VALUES_TOPIC, state_message, retain=True)
    await hass.async_block_till_done()
    assert (hass.states.get("light.badspot_1").state == "on") is lit

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": "light.badspot_1", **data}, blocking=True
    )
    assert publish_log() == expected

    await let_the_fallback_run(hass)
    state = hass.states.get("light.badspot_1")
    assert state.state == "on"
    assert state.attributes["brightness"] == brightness
    if "color_temp_kelvin" in data:
        assert state.attributes["color_temp_kelvin"] == 4600


# ---------------------------------- 8: the last known state is kept ------


async def test_rubbish_leaves_the_last_known_state_readable(
    hass: HomeAssistant, mqtt_mock, make_entry, payload, feed
) -> None:
    """An unusable message never replaces or clears the state before it.

    The gateway keeps the last good state per topic so that an entity added
    later renders immediately (contract in ``contracts.py``). A discarded
    message must not touch that memory, otherwise one broken payload would
    leave every entity added afterwards blank until the next poll.
    """
    entry = await setup_bridge(
        hass,
        make_entry,
        [UnitDefinition(kind=UnitKind.SIMPLE, name="Kochnische", target_id=12)],
    )
    gateway = entry.runtime_data.gateway

    await feed(f"{BASE}/get/poll_device/12/values", payload("unit_values_full"))
    good = gateway.unit_values(12)
    assert good is not None
    assert good.level == 255

    for rubbish in ("", "not json", "[1, 2, 3]", json.dumps({"scene": 0})):
        await feed(f"{BASE}/get/poll_device/12/values", rubbish)

    assert gateway.unit_values(12) == good
    assert gateway.diagnostics().invalid_messages == 4

    # An entity added after the rubbish arrived renders the good value at
    # once, without waiting for the next message on that topic. It is added to
    # the running platform on purpose: editing the configuration would reload
    # the entry, which builds a new gateway with an empty memory.
    platform = next(
        candidate
        for candidate in async_get_platforms(hass, DOMAIN)
        if candidate.domain == "switch"
    )
    await platform.async_add_entities(
        [
            CasambiSwitch(
                gateway,
                UnitDefinition(
                    kind=UnitKind.SWITCH, name="Kochnische Schalter", target_id=12
                ),
            )
        ]
    )
    await hass.async_block_till_done()

    added = hass.states.get("switch.kochnische_schalter")
    assert added is not None
    assert added.state == "on"
