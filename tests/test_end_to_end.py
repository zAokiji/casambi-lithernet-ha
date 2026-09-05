"""End to end tests: the whole integration with mocked MQTT.

Every other test module puts a :class:`FakeGateway` in
place of package B; this one does not. Here the real
:class:`~custom_components.casambi_lithernet.gateway.MqttCasambiGateway` runs,
and only the broker underneath it is a mock. A test therefore exercises the
full chain in both directions:

    service call -> entity -> gateway -> command payload on a topic
    message on a topic -> gateway -> parser -> entity -> Home Assistant state

Three groups of tests:

1. **One per kind.** All seven kinds of project document 6 and 15 are set up,
   commanded and fed a recorded state message.
2. **The reference installation.** The seventeen entities of project document
   section 11, built from the twelve elements they come from, checked for
   entity count, device assignment and unique id collisions.
3. **Restart.** Unloading and setting the entry up again brings every entity
   back with the state from the retained messages the broker replays.

``feed_retained`` is what makes the restart test honest: the gateway publishes
``values`` and ``propertys`` retained (project document 12.3), so the broker
hands them to whoever subscribes next. The mocked broker keeps no messages, so
the test replays them itself with the retain flag set, which is exactly what a
real broker does after a Home Assistant restart.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_mqtt_message,
)

from custom_components.casambi_lithernet.const import (
    DOMAIN,
    TargetType,
    UnitKind,
)
from custom_components.casambi_lithernet.models import UnitDefinition

BASE = "casambi/0"

# ------------------------------------------------------------------ setup --


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


async def feed_retained(hass: HomeAssistant, topic: str, raw: str) -> None:
    """Deliver the retained message a subscriber gets when it subscribes.

    Home Assistant hands a retained message to a subscription exactly once, the
    way a broker does, and ignores every further retained message on the same
    topic. Use this for the first state of a topic and for what comes back
    after a restart; use the ``feed`` fixture for a live update.
    """
    async_fire_mqtt_message(hass, topic, raw, retain=True)
    await hass.async_block_till_done()


def commands_on(
    publish_log: Callable[[], list[tuple[str, dict[str, Any]]]], topic: str
) -> list[dict[str, Any]]:
    """Every payload published on one command topic."""
    return [payload for sent, payload in publish_log() if sent == topic]


def entity_id_of(hass: HomeAssistant, platform: str, unique_id: str) -> str:
    """Look an entity up by its unique id rather than by its name."""
    entity_id = er.async_get(hass).async_get_entity_id(platform, DOMAIN, unique_id)
    assert entity_id is not None, unique_id
    return entity_id


# ---------------------------------------------------- one test per kind --


async def test_simple_unit_round_trip(
    hass: HomeAssistant, mqtt_mock, make_entry, publish_log, payload
) -> None:
    """A simple luminaire dims on one topic and reads its state on another."""
    await setup_bridge(
        hass,
        make_entry,
        [UnitDefinition(kind=UnitKind.SIMPLE, name="Kochnische", target_id=12)],
    )

    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": "light.kochnische", "brightness": 128},
        blocking=True,
    )
    assert commands_on(publish_log, f"{BASE}/set/target_level") == [
        {
            "level": 128,
            "duration": 0,
            "targetid": 12,
            "targettype": int(TargetType.UNIT),
        }
    ]

    await feed_retained(
        hass, f"{BASE}/get/poll_device/12/values", payload("unit_values_on")
    )
    state = hass.states.get("light.kochnische")
    assert state.state == "on"
    assert state.attributes["brightness"] == 4


async def test_tunable_white_round_trip(
    hass: HomeAssistant, mqtt_mock, make_entry, publish_log, payload
) -> None:
    """Colour temperature goes out first, brightness second, on two topics."""
    await setup_bridge(
        hass,
        make_entry,
        [
            UnitDefinition(
                kind=UnitKind.TUNABLE_WHITE,
                name="Badspot 1",
                target_id=20,
                min_kelvin=2700,
                max_kelvin=6500,
            )
        ],
    )

    await hass.services.async_call(
        "light",
        "turn_on",
        {
            "entity_id": "light.badspot_1",
            "brightness": 200,
            "color_temp_kelvin": 6500,
        },
        blocking=True,
    )
    assert [topic for topic, _ in publish_log()] == [
        f"{BASE}/set/target_tc",
        f"{BASE}/set/target_level",
    ]
    assert commands_on(publish_log, f"{BASE}/set/target_tc") == [
        {"tc": 255, "duration": 0, "targetid": 20, "targettype": int(TargetType.UNIT)}
    ]

    await feed_retained(
        hass, f"{BASE}/get/poll_device/20/values", payload("unit_values_full")
    )
    state = hass.states.get("light.badspot_1")
    assert state.state == "on"
    assert state.attributes["brightness"] == 255
    assert state.attributes["color_temp_kelvin"] == 6500


async def test_multi_dali_round_trip(
    hass: HomeAssistant, mqtt_mock, make_entry, publish_log, payload
) -> None:
    """A single driver is addressed by index, the total entity by unit."""
    await setup_bridge(
        hass,
        make_entry,
        [
            UnitDefinition(
                kind=UnitKind.MULTI_DALI,
                name="Wohnzimmer",
                target_id=15,
                dimmer_count=3,
                dimmer_names=("Linear direkt", "Indirekt 1", "Indirekt 2"),
            )
        ],
    )

    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": "light.indirekt_2", "brightness": 64},
        blocking=True,
    )
    assert commands_on(publish_log, f"{BASE}/set/target_dimmers") == [
        {
            "dimmer_index": 2,
            "dimmer_value": 64,
            "duration": 0,
            "targetid": 15,
            "targettype": int(TargetType.UNIT),
        }
    ]
    # A driver is optimistic, so it shows the value it sent right away.
    assert hass.states.get("light.indirekt_2").attributes["brightness"] == 64

    await feed_retained(
        hass, f"{BASE}/get/poll_device/15/values", payload("unit_values_on")
    )
    total = hass.states.get("light.wohnzimmer")
    assert total.state == "on"
    assert total.attributes["brightness"] == 4


async def test_group_round_trip(
    hass: HomeAssistant, mqtt_mock, make_entry, publish_log, payload
) -> None:
    """A group is addressed with target type 2 and reads ``poll_group``."""
    await setup_bridge(
        hass,
        make_entry,
        [UnitDefinition(kind=UnitKind.GROUP, name="Kueche indirekt", target_id=2)],
    )

    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": "light.kueche_indirekt", "brightness": 10},
        blocking=True,
    )
    assert commands_on(publish_log, f"{BASE}/set/target_level") == [
        {
            "level": 10,
            "duration": 0,
            "targetid": 2,
            "targettype": int(TargetType.GROUP),
        }
    ]

    await feed_retained(hass, f"{BASE}/get/poll_group/2", payload("group_values_on"))
    state = hass.states.get("light.kueche_indirekt")
    assert state.state == "on"
    assert state.attributes["brightness"] == 128


async def test_switch_round_trip(
    hass: HomeAssistant, mqtt_mock, make_entry, publish_log, payload, feed
) -> None:
    """A switching output sends level 255 and level 0, nothing else."""
    await setup_bridge(
        hass,
        make_entry,
        [UnitDefinition(kind=UnitKind.SWITCH, name="WC Luefter", target_id=4)],
    )

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.wc_luefter"}, blocking=True
    )
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.wc_luefter"}, blocking=True
    )
    assert [
        command["level"]
        for command in commands_on(publish_log, f"{BASE}/set/target_level")
    ] == [255, 0]

    await feed_retained(
        hass, f"{BASE}/get/poll_device/4/values", payload("unit_values_full")
    )
    assert hass.states.get("switch.wc_luefter").state == "on"
    await feed(f"{BASE}/get/poll_device/4/values", payload("unit_values_off"))
    assert hass.states.get("switch.wc_luefter").state == "off"


async def test_fan_round_trip(
    hass: HomeAssistant, mqtt_mock, make_entry, publish_log, payload
) -> None:
    """The same output in the fan domain sends the same single command."""
    await setup_bridge(
        hass,
        make_entry,
        [
            UnitDefinition(
                kind=UnitKind.SWITCH,
                name="WC Luefter",
                target_id=4,
                switch_domain="fan",
            )
        ],
    )

    await hass.services.async_call(
        "fan", "turn_on", {"entity_id": "fan.wc_luefter"}, blocking=True
    )
    assert commands_on(publish_log, f"{BASE}/set/target_level") == [
        {"level": 255, "duration": 0, "targetid": 4, "targettype": int(TargetType.UNIT)}
    ]

    await feed_retained(
        hass, f"{BASE}/get/poll_device/4/values", payload("unit_values_full")
    )
    assert hass.states.get("fan.wc_luefter").state == "on"


async def test_scene_round_trip(
    hass: HomeAssistant, mqtt_mock, make_entry, publish_log, payload, feed
) -> None:
    """A scene is recalled on its own topic and reads ``poll_scene``."""
    await setup_bridge(
        hass,
        make_entry,
        [UnitDefinition(kind=UnitKind.SCENE, name="Abendlicht", target_id=3)],
    )

    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": "light.abendlicht", "brightness": 180},
        blocking=True,
    )
    assert commands_on(publish_log, f"{BASE}/set/scene_level") == [
        {"scene": 3, "level": 180, "duration": 0}
    ]

    await feed_retained(
        hass, f"{BASE}/get/poll_scene/3", payload("scene_values_active")
    )
    state = hass.states.get("light.abendlicht")
    assert state.state == "on"
    assert state.attributes["brightness"] == 200

    # The recorded inactive scene still carries level 255; it must read as off.
    await feed(f"{BASE}/get/poll_scene/3", payload("scene_values"))
    assert hass.states.get("light.abendlicht").state == "off"


async def test_broadcast_round_trip(
    hass: HomeAssistant, mqtt_mock, make_entry, publish_log, payload
) -> None:
    """Broadcast sets the whole network with one command and no target."""
    await setup_bridge(
        hass,
        make_entry,
        [UnitDefinition(kind=UnitKind.BROADCAST, name="Alle Leuchten")],
    )

    await hass.services.async_call(
        "light", "turn_off", {"entity_id": "light.alle_leuchten"}, blocking=True
    )
    assert commands_on(publish_log, f"{BASE}/set/level") == [
        {"level": 0, "duration": 0}
    ]

    await feed_retained(hass, f"{BASE}/get/poll_broadcast", payload("broadcast_values"))
    state = hass.states.get("light.alle_leuchten")
    assert state.state == "on"
    assert state.attributes["brightness"] == 31
    assert state.attributes["assumed_state"] is True


# --------------------------------------------- the reference installation --

#: The twelve elements behind the seventeen entities of project document 11.
#: The two multi driver units get no total entity, because the installation has
#: none today; that is what makes the entity count come out at seventeen.
INSTALLATION: tuple[UnitDefinition, ...] = (
    UnitDefinition(kind=UnitKind.SIMPLE, name="Spots Arbeitsplatte", target_id=19),
    UnitDefinition(kind=UnitKind.SIMPLE, name="Esstisch", target_id=18),
    UnitDefinition(kind=UnitKind.SIMPLE, name="Kochnische", target_id=12),
    UnitDefinition(kind=UnitKind.SIMPLE, name="WC", target_id=9),
    UnitDefinition(kind=UnitKind.GROUP, name="Kueche indirekt", target_id=2),
    UnitDefinition(
        kind=UnitKind.MULTI_DALI,
        name="Wohnzimmer",
        target_id=15,
        dimmer_count=3,
        dimmer_names=(
            "Wohnzimmer Linear direkt",
            "Wohnzimmer indirekt 1",
            "Wohnzimmer indirekt 2",
        ),
        with_total_entity=False,
    ),
    UnitDefinition(
        kind=UnitKind.MULTI_DALI,
        name="Gang",
        target_id=16,
        dimmer_count=4,
        dimmer_names=("Gang Spot 1", "Gang Spot 2", "Vorraum", "Spiegellicht"),
        with_total_entity=False,
    ),
    UnitDefinition(kind=UnitKind.TUNABLE_WHITE, name="Badspiegel", target_id=17),
    UnitDefinition(kind=UnitKind.TUNABLE_WHITE, name="Badspot 1", target_id=20),
    UnitDefinition(kind=UnitKind.TUNABLE_WHITE, name="Badspot 2", target_id=21),
    UnitDefinition(kind=UnitKind.TUNABLE_WHITE, name="Badspot 3", target_id=22),
    UnitDefinition(kind=UnitKind.SWITCH, name="WC Luefter", target_id=4),
)

#: The seventeen entity ids the migration table of section 11 produces.
EXPECTED_ENTITY_IDS: frozenset[str] = frozenset(
    {
        "light.spots_arbeitsplatte",
        "light.esstisch",
        "light.kochnische",
        "light.wc",
        "light.kueche_indirekt",
        "light.wohnzimmer_linear_direkt",
        "light.wohnzimmer_indirekt_1",
        "light.wohnzimmer_indirekt_2",
        "light.gang_spot_1",
        "light.gang_spot_2",
        "light.vorraum",
        "light.spiegellicht",
        "light.badspiegel",
        "light.badspot_1",
        "light.badspot_2",
        "light.badspot_3",
        "switch.wc_luefter",
    }
)


@pytest.fixture
async def installation(hass: HomeAssistant, mqtt_mock, make_entry) -> MockConfigEntry:
    """Set up the reference installation of project document section 11."""
    return await setup_bridge(hass, make_entry, INSTALLATION)


def _entries(hass: HomeAssistant, entry: MockConfigEntry) -> list[er.RegistryEntry]:
    """Every registry entry of this config entry, disabled ones included."""
    return er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)


async def test_installation_produces_seventeen_controls(
    hass: HomeAssistant, installation
) -> None:
    """Twelve elements become exactly the seventeen entities of section 11."""
    controls = {
        registry_entry.entity_id
        for registry_entry in _entries(hass, installation)
        if registry_entry.domain in ("light", "switch", "fan")
    }
    assert controls == EXPECTED_ENTITY_IDS
    assert len(controls) == 17


async def test_installation_has_no_duplicate_unique_ids(
    hass: HomeAssistant, installation
) -> None:
    """No two entities of the installation share a unique id.

    Diagnostic entities are included, disabled ones as well: a collision there
    would silently drop an entity instead of raising anything.
    """
    unique_ids = [entry.unique_id for entry in _entries(hass, installation)]
    assert len(unique_ids) == len(set(unique_ids))
    assert len(unique_ids) > 17


async def test_installation_groups_drivers_on_one_device(
    hass: HomeAssistant, installation
) -> None:
    """Each element is one device below the gateway, drivers included."""
    devices = dr.async_get(hass)
    entities = er.async_get(hass)

    gateway_device = devices.async_get_device(identifiers={(DOMAIN, f"{DOMAIN}_0")})
    assert gateway_device is not None

    element_devices = {
        device.id
        for device in dr.async_entries_for_config_entry(devices, installation.entry_id)
        if device.id != gateway_device.id
    }
    assert len(element_devices) == len(INSTALLATION)
    for device_id in element_devices:
        assert devices.async_get(device_id).via_device_id == gateway_device.id

    # The four drivers of unit 16 sit on one device, as does the group.
    gang = devices.async_get_device(identifiers={(DOMAIN, f"{DOMAIN}_0_u16")})
    driver_entities = {
        entry.entity_id
        for entry in er.async_entries_for_device(entities, gang.id)
        if entry.domain == "light"
    }
    assert driver_entities == {
        "light.gang_spot_1",
        "light.gang_spot_2",
        "light.vorraum",
        "light.spiegellicht",
    }


async def test_installation_addresses_every_element_correctly(
    hass: HomeAssistant, installation, publish_log
) -> None:
    """Each kind reaches the wire with its own topic and target type."""
    for entity_id in ("light.kochnische", "light.kueche_indirekt", "light.gang_spot_1"):
        await hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": entity_id, "brightness": 42},
            blocking=True,
        )
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.wc_luefter"}, blocking=True
    )

    assert publish_log() == [
        (
            f"{BASE}/set/target_level",
            {"level": 42, "duration": 0, "targetid": 12, "targettype": 1},
        ),
        (
            f"{BASE}/set/target_level",
            {"level": 42, "duration": 0, "targetid": 2, "targettype": 2},
        ),
        (
            f"{BASE}/set/target_dimmers",
            {
                "dimmer_index": 0,
                "dimmer_value": 42,
                "duration": 0,
                "targetid": 16,
                "targettype": 1,
            },
        ),
        (
            f"{BASE}/set/target_level",
            {"level": 255, "duration": 0, "targetid": 4, "targettype": 1},
        ),
    ]


# ---------------------------------------------------------------- restart --


async def test_restart_restores_every_entity_and_its_state(
    hass: HomeAssistant, mqtt_mock, make_entry, payload
) -> None:
    """After a reload every entity is back and reads the retained messages.

    This is the Home Assistant restart of the acceptance checklist in section
    10: the entry is unloaded, set up again, and the broker replays what the
    gateway published retained.
    """
    entry = await setup_bridge(hass, make_entry, INSTALLATION)

    async def replay_retained() -> None:
        for unit_id in (19, 18, 12, 9, 15, 16, 17, 20, 21, 22, 4):
            await feed_retained(
                hass,
                f"{BASE}/get/poll_device/{unit_id}/values",
                payload("unit_values_full"),
            )
            await feed_retained(
                hass,
                f"{BASE}/get/poll_device/{unit_id}/propertys",
                payload("unit_properties_online"),
            )

    await replay_retained()
    before = {
        entity_id: hass.states.get(entity_id).state for entity_id in EXPECTED_ENTITY_IDS
    }
    assert before["light.kochnische"] == "on"
    assert before["switch.wc_luefter"] == "on"

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    unloaded = hass.states.get("light.kochnische")
    assert unloaded.state == "unavailable"
    assert unloaded.attributes["restored"] is True

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    for entity_id in EXPECTED_ENTITY_IDS:
        assert hass.states.get(entity_id) is not None, entity_id

    await replay_retained()
    after = {
        entity_id: hass.states.get(entity_id).state for entity_id in EXPECTED_ENTITY_IDS
    }
    # The seven drivers stay optimistic and therefore unknown until commanded;
    # everything the gateway reports is back on.
    assert after == before


async def test_restart_keeps_the_entity_ids(
    hass: HomeAssistant, mqtt_mock, make_entry
) -> None:
    """A reload never renames an entity, so dashboards keep working."""
    entry = await setup_bridge(hass, make_entry, INSTALLATION)
    entities = er.async_get(hass)
    before = {
        registry_entry.unique_id: registry_entry.entity_id
        for registry_entry in _entries(hass, entry)
    }

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    after = {
        registry_entry.unique_id: registry_entry.entity_id
        for registry_entry in _entries(hass, entry)
    }
    assert after == before
    assert entities.async_get_entity_id("switch", DOMAIN, f"{DOMAIN}_0_u4") == (
        "switch.wc_luefter"
    )


async def test_unloading_drops_every_subscription(
    hass: HomeAssistant, mqtt_mock, make_entry, payload
) -> None:
    """A message after unloading reaches nobody and raises nothing."""
    entry = await setup_bridge(
        hass,
        make_entry,
        [UnitDefinition(kind=UnitKind.SIMPLE, name="Kochnische", target_id=12)],
    )
    gateway = entry.runtime_data.gateway
    assert gateway.diagnostics().subscribed_topics

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    before = gateway.diagnostics().messages_received
    await feed_retained(
        hass, f"{BASE}/get/poll_device/12/values", payload("unit_values_on")
    )
    assert gateway.diagnostics().messages_received == before


async def test_one_subscription_serves_several_entities(
    hass: HomeAssistant, mqtt_mock, make_entry, payload
) -> None:
    """The four drivers and the total entity share one values subscription."""
    entry = await setup_bridge(
        hass,
        make_entry,
        [
            UnitDefinition(
                kind=UnitKind.MULTI_DALI,
                name="Gang",
                target_id=16,
                dimmer_count=4,
            )
        ],
    )
    subscribed = entry.runtime_data.gateway.diagnostics().subscribed_topics
    assert sorted(subscribed) == [
        f"{BASE}/get/poll_device/16/propertys",
        f"{BASE}/get/poll_device/16/values",
    ]


async def test_diagnostic_sensors_read_the_recorded_properties(
    hass: HomeAssistant, mqtt_mock, make_entry, payload
) -> None:
    """A recorded ``propertys`` message reaches the diagnostic entities."""
    await setup_bridge(
        hass,
        make_entry,
        [UnitDefinition(kind=UnitKind.SIMPLE, name="Kochnische", target_id=12)],
    )
    await feed_retained(
        hass,
        f"{BASE}/get/poll_device/12/propertys",
        payload("unit_properties_lamp_failure"),
    )

    problem = entity_id_of(hass, "binary_sensor", f"{DOMAIN}_0_u12_problem")
    condition = entity_id_of(hass, "sensor", f"{DOMAIN}_0_u12_condition")
    source = entity_id_of(hass, "sensor", f"{DOMAIN}_0_u12_priority_source")
    assert hass.states.get(problem).state == "on"
    assert hass.states.get(condition).state == "lamp_failure"
    assert hass.states.get(source).state == "presence"


async def test_unusable_payload_leaves_the_state_untouched(
    hass: HomeAssistant, mqtt_mock, make_entry, payload, feed
) -> None:
    """Rubbish on a state topic is counted, logged and otherwise ignored."""
    entry = await setup_bridge(
        hass,
        make_entry,
        [UnitDefinition(kind=UnitKind.SIMPLE, name="Kochnische", target_id=12)],
    )
    await feed_retained(
        hass, f"{BASE}/get/poll_device/12/values", payload("unit_values_on")
    )
    assert hass.states.get("light.kochnische").attributes["brightness"] == 4

    for rubbish in ("", "not json", "[1, 2, 3]", json.dumps({"scene": 0})):
        await feed(f"{BASE}/get/poll_device/12/values", rubbish)

    assert hass.states.get("light.kochnische").attributes["brightness"] == 4
    assert entry.runtime_data.gateway.diagnostics().invalid_messages == 4


async def test_changing_the_driver_count_adds_and_removes_entities(
    hass: HomeAssistant, mqtt_mock, make_entry
) -> None:
    """Editing ``dimmer_count`` adds or drops drivers and keeps the rest.

    The acceptance list of section 10 asks for this explicitly: the four
    drivers of unit 16 must survive a correction of the count, because their
    entity ids are in dashboards and automations.
    """
    entry = await setup_bridge(
        hass,
        make_entry,
        [
            UnitDefinition(
                kind=UnitKind.MULTI_DALI,
                name="Gang",
                target_id=16,
                dimmer_count=4,
                dimmer_names=("Gang Spot 1", "Gang Spot 2", "Vorraum", "Spiegellicht"),
                with_total_entity=False,
            )
        ],
    )
    entities = er.async_get(hass)

    def driver_ids() -> list[str | None]:
        return [
            entities.async_get_entity_id("light", DOMAIN, f"{DOMAIN}_0_u16_d{index}")
            for index in range(5)
        ]

    assert driver_ids() == [
        "light.gang_spot_1",
        "light.gang_spot_2",
        "light.vorraum",
        "light.spiegellicht",
        None,
    ]

    subentry = next(iter(entry.subentries.values()))
    grown = UnitDefinition(
        kind=UnitKind.MULTI_DALI,
        name="Gang",
        target_id=16,
        dimmer_count=5,
        dimmer_names=("Gang Spot 1", "Gang Spot 2", "Vorraum", "Spiegellicht"),
        with_total_entity=False,
    )
    hass.config_entries.async_update_subentry(entry, subentry, data=grown.to_dict())
    await hass.async_block_till_done()

    # The fifth driver appeared with its numbered fallback name, the four
    # existing ones kept their entity ids.
    assert driver_ids()[:4] == [
        "light.gang_spot_1",
        "light.gang_spot_2",
        "light.vorraum",
        "light.spiegellicht",
    ]
    assert driver_ids()[4] == "light.gang_dimmer_5"
    assert hass.states.get("light.gang_dimmer_5") is not None

    shrunk = UnitDefinition(
        kind=UnitKind.MULTI_DALI,
        name="Gang",
        target_id=16,
        dimmer_count=2,
        dimmer_names=("Gang Spot 1", "Gang Spot 2"),
        with_total_entity=False,
    )
    hass.config_entries.async_update_subentry(entry, subentry, data=shrunk.to_dict())
    await hass.async_block_till_done()

    # Shrinking has to remove the entities of the drivers that are gone, not
    # leave them behind as permanently unavailable ones. The same goes for the
    # total entity that was switched off.
    assert hass.states.get("light.gang_spot_1") is not None
    registry = er.async_get(hass)
    for gone in ("light.vorraum", "light.spiegellicht", "light.gang_dimmer_5"):
        assert hass.states.get(gone) is None
        assert registry.async_get(gone) is None
    assert registry.async_get_entity_id("light", DOMAIN, "casambi_lithernet_0_u16") is (
        None
    )
