"""Regression tests for the pitfalls of docs/DESIGN.md, "Der wichtigste Fallstrick".

Every test here exists because the behaviour it checks was
already wrong once, in the YAML setup this integration replaces. They run
against the real gateway with a mocked broker, so a regression has to survive
the whole chain to slip through, not just a unit test of one class.

The five pitfalls:

1. **Exactly one command when switching on with a brightness.** The old YAML
   needed ``on_command_type: brightness`` because Home Assistant otherwise
   sends a separate "on" with level 255 that overwrites the brightness or the
   colour temperature that went with it. This is the most important test of the
   project, so it is checked for every kind that can be dimmed.
2. **A unique id never depends on the name.** Renaming an element must keep its
   entity, its entity id and its history.
3. **No ``_2`` suffix.** Removing an element and adding it again under the same
   name must give back the same entity id; a suffix silently breaks every
   automation and dashboard that names the entity.
4. **The colour temperature survives an incoming state message.** The gateway
   reports ``cct_level: 0`` for every unit, tunable white included, so a values
   message must not be read as "colour temperature is at the bottom".
5. **A broken message breaks nothing.** Anything that is not a usable payload
   is discarded; the entity keeps the state it had.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from types import MappingProxyType
from typing import Any

import pytest
from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.casambi_lithernet.const import (
    DOMAIN,
    SUBENTRY_TYPE_UNIT,
    UnitKind,
)
from custom_components.casambi_lithernet.models import UnitDefinition

BASE = "casambi/0"

SIMPLE = UnitDefinition(kind=UnitKind.SIMPLE, name="Kochnische", target_id=12)
TUNABLE = UnitDefinition(
    kind=UnitKind.TUNABLE_WHITE, name="Badspot 1", target_id=20, default_on_level=200
)
GROUP = UnitDefinition(kind=UnitKind.GROUP, name="Kueche indirekt", target_id=2)
MULTI = UnitDefinition(
    kind=UnitKind.MULTI_DALI,
    name="Gang",
    target_id=16,
    dimmer_count=2,
    dimmer_names=("Gang Spot 1", "Gang Spot 2"),
    with_total_entity=False,
)
SCENE = UnitDefinition(kind=UnitKind.SCENE, name="Abendlicht", target_id=3)
BROADCAST = UnitDefinition(kind=UnitKind.BROADCAST, name="Alle Leuchten")
SWITCH = UnitDefinition(kind=UnitKind.SWITCH, name="WC Luefter", target_id=4)


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


# ------------------------------- 2.5: exactly one command when switching on --


@pytest.mark.parametrize(
    ("unit", "entity_id", "expected_topic"),
    [
        (SIMPLE, "light.kochnische", f"{BASE}/set/target_level"),
        (GROUP, "light.kueche_indirekt", f"{BASE}/set/target_level"),
        (MULTI, "light.gang_spot_1", f"{BASE}/set/target_dimmers"),
        (SCENE, "light.abendlicht", f"{BASE}/set/scene_level"),
        (BROADCAST, "light.alle_leuchten", f"{BASE}/set/level"),
    ],
)
async def test_turning_on_with_a_brightness_is_one_message(
    hass: HomeAssistant,
    mqtt_mock,
    make_entry,
    publish_log,
    unit,
    entity_id,
    expected_topic,
) -> None:
    """The single most important guarantee: one message, and no level 255.

    A second command with level 255 would arrive after the brightness and
    overwrite it, which is what the old YAML needed ``on_command_type:
    brightness`` to prevent.
    """
    await setup_bridge(hass, make_entry, [unit])

    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": entity_id, "brightness": 37},
        blocking=True,
    )

    sent = publish_log()
    assert len(sent) == 1
    topic, message = sent[0]
    assert topic == expected_topic
    assert 255 not in message.values()
    assert 37 in message.values()


async def test_tunable_white_sends_one_command_per_thing_that_changed(
    hass: HomeAssistant, mqtt_mock, make_entry, publish_log
) -> None:
    """Colour temperature and brightness are one command each, never three.

    The order matters as much as the count: the colour temperature has to be on
    the wire before the level, because the level command is the one that ends
    up switching the luminaire on.
    """
    await setup_bridge(hass, make_entry, [TUNABLE])

    await hass.services.async_call(
        "light",
        "turn_on",
        {
            "entity_id": "light.badspot_1",
            "brightness": 90,
            "color_temp_kelvin": 4600,
        },
        blocking=True,
    )

    assert publish_log() == [
        (
            f"{BASE}/set/target_tc",
            {"tc": 128, "duration": 0, "targetid": 20, "targettype": 1},
        ),
        (
            f"{BASE}/set/target_level",
            {"level": 90, "duration": 0, "targetid": 20, "targettype": 1},
        ),
    ]


async def test_switching_on_without_a_brightness_is_still_one_message(
    hass: HomeAssistant, mqtt_mock, make_entry, publish_log
) -> None:
    """Without a brightness the configured default goes out, once."""
    await setup_bridge(hass, make_entry, [TUNABLE])

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": "light.badspot_1"}, blocking=True
    )

    assert publish_log() == [
        (
            f"{BASE}/set/target_level",
            {"level": 200, "duration": 0, "targetid": 20, "targettype": 1},
        )
    ]


async def test_changing_only_the_colour_temperature_sends_no_level(
    hass: HomeAssistant, mqtt_mock, make_entry, publish_log, feed, payload
) -> None:
    """A warmer setting on a lit luminaire must not reset its brightness."""
    await setup_bridge(hass, make_entry, [TUNABLE])
    await feed(f"{BASE}/get/poll_device/20/values", payload("unit_values_on"))
    assert hass.states.get("light.badspot_1").state == "on"

    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": "light.badspot_1", "color_temp_kelvin": 2700},
        blocking=True,
    )

    assert publish_log() == [
        (
            f"{BASE}/set/target_tc",
            {"tc": 0, "duration": 0, "targetid": 20, "targettype": 1},
        )
    ]
    assert hass.states.get("light.badspot_1").attributes["brightness"] == 4


async def test_switch_on_and_off_are_one_message_each(
    hass: HomeAssistant, mqtt_mock, make_entry, publish_log
) -> None:
    """The switching output has no separate on command either."""
    await setup_bridge(hass, make_entry, [SWITCH])

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.wc_luefter"}, blocking=True
    )
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.wc_luefter"}, blocking=True
    )

    assert publish_log() == [
        (
            f"{BASE}/set/target_level",
            {"level": 255, "duration": 0, "targetid": 4, "targettype": 1},
        ),
        (
            f"{BASE}/set/target_level",
            {"level": 0, "duration": 0, "targetid": 4, "targettype": 1},
        ),
    ]


# ------------------------------------ 2.5: the unique id ignores the name --


async def test_renaming_an_element_keeps_its_entity(
    hass: HomeAssistant, mqtt_mock, make_entry
) -> None:
    """A rename changes the friendly name and nothing else.

    Unique id, entity id and therefore every automation, scene and dashboard
    that references the entity survive it.
    """
    entry = await setup_bridge(hass, make_entry, [SIMPLE])
    entities = er.async_get(hass)
    unique_id = SIMPLE.base_unique_id(0)
    before = entities.async_get_entity_id("light", DOMAIN, unique_id)
    assert before == "light.kochnische"

    subentry = next(iter(entry.subentries.values()))
    renamed = UnitDefinition(
        kind=UnitKind.SIMPLE, name="Kueche Kochnische", target_id=12
    )
    hass.config_entries.async_update_subentry(
        entry, subentry, data=renamed.to_dict(), title=renamed.name
    )
    await hass.async_block_till_done()

    assert entities.async_get_entity_id("light", DOMAIN, unique_id) == before
    assert hass.states.get(before) is not None
    assert hass.states.get(before).attributes["friendly_name"] == "Kueche Kochnische"
    # And nothing new appeared next to it.
    assert [
        registry_entry.entity_id
        for registry_entry in er.async_entries_for_config_entry(
            entities, entry.entry_id
        )
        if registry_entry.domain == "light"
    ] == [before]


async def test_renaming_a_driver_keeps_every_dimmer_entity(
    hass: HomeAssistant, mqtt_mock, make_entry
) -> None:
    """Renaming one driver of a multi driver unit leaves the others alone."""
    entry = await setup_bridge(hass, make_entry, [MULTI])
    entities = er.async_get(hass)
    before = {
        MULTI.dimmer_unique_id(0, index): entities.async_get_entity_id(
            "light", DOMAIN, MULTI.dimmer_unique_id(0, index)
        )
        for index in range(MULTI.dimmer_count)
    }
    assert before[MULTI.dimmer_unique_id(0, 1)] == "light.gang_spot_2"

    subentry = next(iter(entry.subentries.values()))
    renamed = UnitDefinition(
        kind=UnitKind.MULTI_DALI,
        name="Gang",
        target_id=16,
        dimmer_count=2,
        dimmer_names=("Gang Spot 1", "Gang Spot zwei"),
        with_total_entity=False,
    )
    hass.config_entries.async_update_subentry(entry, subentry, data=renamed.to_dict())
    await hass.async_block_till_done()

    after = {
        unique_id: entities.async_get_entity_id("light", DOMAIN, unique_id)
        for unique_id in before
    }
    assert after == before


# ------------------------------------- 2.5: no ``_2`` suffix on a new try --


async def test_removing_and_adding_an_element_reuses_the_entity_id(
    hass: HomeAssistant, mqtt_mock, make_entry
) -> None:
    """Adding an element again under the same name gives the same entity id.

    The registry collision of the migration (``light.badspot_1_2``) came from
    an *old* entity that was still registered. Once the element is properly
    removed, adding it again must land on the original id, otherwise every
    correction of a typo would rename the entity behind the user's back.
    """
    entry = await setup_bridge(hass, make_entry, [SIMPLE])
    entities = er.async_get(hass)
    before = entities.async_get_entity_id("light", DOMAIN, SIMPLE.base_unique_id(0))
    assert before == "light.kochnische"

    subentry = next(iter(entry.subentries.values()))
    assert hass.config_entries.async_remove_subentry(entry, subentry.subentry_id)
    await hass.async_block_till_done()
    assert entities.async_get_entity_id("light", DOMAIN, SIMPLE.base_unique_id(0)) is (
        None
    )

    hass.config_entries.async_add_subentry(
        entry,
        ConfigSubentry(
            data=MappingProxyType(SIMPLE.to_dict()),
            subentry_type=SUBENTRY_TYPE_UNIT,
            title=SIMPLE.name,
            unique_id=None,
        ),
    )
    await hass.async_block_till_done()

    after = entities.async_get_entity_id("light", DOMAIN, SIMPLE.base_unique_id(0))
    assert after == before
    assert not after.endswith("_2")


async def test_a_second_element_with_another_name_gets_no_suffix_either(
    hass: HomeAssistant, mqtt_mock, make_entry
) -> None:
    """Two elements of the same unit id in different domains stay distinct.

    Section 7 allows the same Casambi id with a different kind. The unique ids
    encode the address space, so neither entity has to fall back to a suffix.
    """
    await setup_bridge(
        hass,
        make_entry,
        [
            UnitDefinition(kind=UnitKind.SIMPLE, name="Kochnische", target_id=12),
            UnitDefinition(kind=UnitKind.GROUP, name="Kochnische Gruppe", target_id=12),
        ],
    )
    entity_ids = sorted(
        entry.entity_id
        for entry in er.async_entries_for_config_entry(
            er.async_get(hass),
            next(iter(hass.config_entries.async_entries(DOMAIN))).entry_id,
        )
        if entry.domain == "light"
    )
    assert entity_ids == ["light.kochnische", "light.kochnische_gruppe"]


# ------------------- 2.5: the colour temperature is never reported back --


async def test_colour_temperature_survives_a_values_message(
    hass: HomeAssistant, mqtt_mock, make_entry, feed, payload
) -> None:
    """``cct_level: 0`` in a values message must not clear what was set.

    The gateway reports 0 for every unit, tunable white included (capture of
    2026-09-04), so the colour temperature is kept optimistically while the
    brightness stays real. The brightness of a non optimistic luminaire only
    reaches Home Assistant once the gateway confirms it, which is why the first
    check comes after the values message rather than after the service call.
    """
    await setup_bridge(hass, make_entry, [TUNABLE])
    recorded = json.loads(payload("unit_values_on"))
    assert recorded["cct_level"] == 0, "the fixture is the point of this test"

    await hass.services.async_call(
        "light",
        "turn_on",
        {
            "entity_id": "light.badspot_1",
            "brightness": 90,
            "color_temp_kelvin": 4600,
        },
        blocking=True,
    )
    await feed(f"{BASE}/get/poll_device/20/values", payload("unit_values_on"))

    state = hass.states.get("light.badspot_1")
    assert state.attributes["brightness"] == 4, "the brightness is real"
    assert state.attributes["color_temp_kelvin"] == 4600, "the kelvin value is kept"

    # Going off and on again keeps it too. Home Assistant hides the colour
    # attributes of a light that is off, so the check is on the way back.
    await feed(f"{BASE}/get/poll_device/20/values", payload("unit_values_off"))
    assert hass.states.get("light.badspot_1").state == "off"
    await feed(f"{BASE}/get/poll_device/20/values", payload("unit_values_full"))
    back = hass.states.get("light.badspot_1")
    assert back.attributes["brightness"] == 255
    assert back.attributes["color_temp_kelvin"] == 4600


async def test_colour_temperature_survives_a_restart_of_the_dimming(
    hass: HomeAssistant, mqtt_mock, make_entry, feed, payload
) -> None:
    """Dimming afterwards keeps the colour temperature as well."""
    await setup_bridge(hass, make_entry, [TUNABLE])
    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": "light.badspot_1", "color_temp_kelvin": 2700},
        blocking=True,
    )
    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": "light.badspot_1", "brightness": 30},
        blocking=True,
    )
    await feed(f"{BASE}/get/poll_device/20/values", payload("unit_values_on"))

    assert hass.states.get("light.badspot_1").attributes["color_temp_kelvin"] == 2700


# --------------------------------- 2.5: a broken message breaks nothing --

#: Payloads that are not a usable state message, from the harmless to the
#: actively hostile.
RUBBISH: tuple[str, ...] = (
    "",
    "   ",
    "not json at all",
    "null",
    "42",
    "[1, 2, 3]",
    '{"level": "bright"}',
    '{"level": null}',
    '{"scene": 0, "last_change": 1}',
    '{"level": {"nested": 1}}',
)


@pytest.mark.parametrize(
    ("unit", "topic", "entity_id"),
    [
        (SIMPLE, f"{BASE}/get/poll_device/12/values", "light.kochnische"),
        (GROUP, f"{BASE}/get/poll_group/2", "light.kueche_indirekt"),
        (SCENE, f"{BASE}/get/poll_scene/3", "light.abendlicht"),
        (BROADCAST, f"{BASE}/get/poll_broadcast", "light.alle_leuchten"),
        (SWITCH, f"{BASE}/get/poll_device/4/values", "switch.wc_luefter"),
    ],
)
async def test_unusable_messages_do_not_break_an_entity(
    hass: HomeAssistant, mqtt_mock, make_entry, feed, unit, topic, entity_id
) -> None:
    """Every state topic tolerates rubbish without losing its entity."""
    entry = await setup_bridge(hass, make_entry, [unit])

    for rubbish in RUBBISH:
        await feed(topic, rubbish)

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state != "unavailable"
    assert entry.runtime_data.gateway.diagnostics().invalid_messages == len(RUBBISH)


async def test_unusable_properties_leave_availability_alone(
    hass: HomeAssistant, mqtt_mock, make_entry, feed, payload
) -> None:
    """A broken ``propertys`` message must not take a luminaire offline."""
    await setup_bridge(hass, make_entry, [SIMPLE])
    await feed(
        f"{BASE}/get/poll_device/12/propertys", payload("unit_properties_online")
    )
    await feed(f"{BASE}/get/poll_device/12/values", payload("unit_values_on"))
    assert hass.states.get("light.kochnische").state == "on"

    for rubbish in RUBBISH:
        await feed(f"{BASE}/get/poll_device/12/propertys", rubbish)

    assert hass.states.get("light.kochnische").state == "on"


async def test_a_message_on_a_topic_nobody_wants_changes_nothing(
    hass: HomeAssistant, mqtt_mock, make_entry, feed, payload
) -> None:
    """Traffic for other units, scene calls and deletions are ignored.

    The capture of 2026-09-05 shows all of these on the bridge; none of them is
    subscribed, so none of them may reach an entity.
    """
    await setup_bridge(hass, make_entry, [SIMPLE])
    await feed(f"{BASE}/get/poll_device/12/values", payload("unit_values_on"))
    assert hass.states.get("light.kochnische").attributes["brightness"] == 4

    await feed(f"{BASE}/get/poll_device/99/values", payload("unit_values_full"))
    await feed(f"{BASE}/get/scene_call", payload("scene_call"))
    await feed(f"{BASE}/get/node_deleted/", payload("node_deleted"))
    await feed(f"{BASE}/get/poll_ungrouped", payload("ungrouped_values"))

    assert hass.states.get("light.kochnische").attributes["brightness"] == 4


async def test_a_broken_element_definition_does_not_stop_the_others(
    hass: HomeAssistant, mqtt_mock, make_entry
) -> None:
    """One unusable subentry is skipped, the rest of the bridge still loads."""
    entry = make_entry(units=[SIMPLE.to_dict()])
    entry.add_to_hass(hass)
    hass.config_entries.async_add_subentry(
        entry,
        ConfigSubentry(
            data=MappingProxyType({"kind": "not_a_kind", "name": "Kaputt"}),
            subentry_type=SUBENTRY_TYPE_UNIT,
            title="Kaputt",
            unique_id=None,
        ),
    )
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("light.kochnische") is not None
    assert len(entry.runtime_data.units) == 1
