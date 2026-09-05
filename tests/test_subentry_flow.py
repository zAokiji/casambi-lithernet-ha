"""Package D tests: adding, editing and validating Casambi elements."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.casambi_lithernet.const import (
    DOMAIN,
    IMPLEMENTED_KINDS,
    SUBENTRY_TYPE_UNIT,
    UnitKind,
)

COMPONENT_DIR = Path(__file__).parent.parent / "custom_components" / "casambi_lithernet"


async def _start(hass: HomeAssistant, entry, step: str) -> dict[str, Any]:
    """Open the add flow and walk into the form of one kind."""
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_UNIT), context={"source": "user"}
    )
    assert result["type"] is FlowResultType.MENU
    assert set(result["menu_options"]) == {str(kind) for kind in IMPLEMENTED_KINDS}
    return await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": step}
    )


async def _add(hass: HomeAssistant, entry, step: str, user_input: dict[str, Any]):
    """Add one element and return the raw flow result."""
    form = await _start(hass, entry, step)
    assert form["type"] is FlowResultType.FORM
    assert form["step_id"] == step
    return await hass.config_entries.subentries.async_configure(
        form["flow_id"], user_input
    )


def _only_subentry(entry):
    subentries = [
        sub
        for sub in entry.subentries.values()
        if sub.subentry_type == SUBENTRY_TYPE_UNIT
    ]
    assert len(subentries) == 1
    return subentries[0]


# --------------------------------------------------------------- adding --


@pytest.mark.parametrize(
    ("step", "user_input", "expected"),
    [
        (
            "simple",
            {
                "name": "Esstisch",
                "target_id": 18,
                "default_on_level": 200,
                "optimistic_override": False,
            },
            {
                "kind": "simple",
                "name": "Esstisch",
                "target_id": 18,
                "min_kelvin": 2700,
                "max_kelvin": 6500,
                "dimmer_count": 1,
                "dimmer_names": [],
                "with_total_entity": True,
                "default_on_level": 200,
                "optimistic_override": False,
                "switch_domain": "switch",
            },
        ),
        (
            "tunable_white",
            {
                "name": "Badspiegel",
                "target_id": 17,
                "min_kelvin": 2200,
                "max_kelvin": 6000,
                "default_on_level": 255,
                "optimistic_override": True,
            },
            {
                "kind": "tunable_white",
                "name": "Badspiegel",
                "target_id": 17,
                "min_kelvin": 2200,
                "max_kelvin": 6000,
                "dimmer_count": 1,
                "dimmer_names": [],
                "with_total_entity": True,
                "default_on_level": 255,
                "optimistic_override": True,
                "switch_domain": "switch",
            },
        ),
        (
            "multi_dali",
            {
                "name": "Gang",
                "target_id": 16,
                "dimmer_count": 4,
                "dimmer_names": "Gang Spot 1\nGang Spot 2\nVorraum\nSpiegellicht",
                "with_total_entity": False,
                "default_on_level": 128,
            },
            {
                "kind": "multi_dali",
                "name": "Gang",
                "target_id": 16,
                "min_kelvin": 2700,
                "max_kelvin": 6500,
                "dimmer_count": 4,
                "dimmer_names": [
                    "Gang Spot 1",
                    "Gang Spot 2",
                    "Vorraum",
                    "Spiegellicht",
                ],
                "with_total_entity": False,
                "default_on_level": 128,
                "optimistic_override": False,
                "switch_domain": "switch",
            },
        ),
        (
            "group",
            {"name": "Küche indirekt", "target_id": 2, "default_on_level": 255},
            {
                "kind": "group",
                "name": "Küche indirekt",
                "target_id": 2,
                "min_kelvin": 2700,
                "max_kelvin": 6500,
                "dimmer_count": 1,
                "dimmer_names": [],
                "with_total_entity": True,
                "default_on_level": 255,
                "optimistic_override": False,
                "switch_domain": "switch",
            },
        ),
        (
            "switch",
            {"name": "WC Lüfter", "target_id": 4, "switch_domain": "fan"},
            {
                "kind": "switch",
                "name": "WC Lüfter",
                "target_id": 4,
                "min_kelvin": 2700,
                "max_kelvin": 6500,
                "dimmer_count": 1,
                "dimmer_names": [],
                "with_total_entity": True,
                "default_on_level": 255,
                "optimistic_override": False,
                "switch_domain": "fan",
            },
        ),
        (
            "scene",
            {"name": "Abend", "target_id": 3, "default_on_level": 180},
            {
                "kind": "scene",
                "name": "Abend",
                "target_id": 3,
                "min_kelvin": 2700,
                "max_kelvin": 6500,
                "dimmer_count": 1,
                "dimmer_names": [],
                "with_total_entity": True,
                "default_on_level": 180,
                "optimistic_override": False,
                "switch_domain": "switch",
            },
        ),
        (
            "broadcast",
            {"name": "Alle Leuchten", "default_on_level": 255},
            {
                "kind": "broadcast",
                "name": "Alle Leuchten",
                "target_id": 0,
                "min_kelvin": 2700,
                "max_kelvin": 6500,
                "dimmer_count": 1,
                "dimmer_names": [],
                "with_total_entity": True,
                "default_on_level": 255,
                "optimistic_override": False,
                "switch_domain": "switch",
            },
        ),
    ],
    ids=[str(kind) for kind in IMPLEMENTED_KINDS],
)
async def test_add_every_kind_stores_expected_data(
    hass: HomeAssistant, casambi_entry, step, user_input, expected
) -> None:
    """Every kind can be added and stores exactly the expected fields."""
    result = await _add(hass, casambi_entry, step, user_input)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == expected["name"]

    subentry = _only_subentry(casambi_entry)
    assert dict(subentry.data) == expected
    assert subentry.title == expected["name"]


async def test_broadcast_form_has_only_a_name(
    hass: HomeAssistant, casambi_entry
) -> None:
    """Broadcast has no address, so the form must not ask for one."""
    form = await _start(hass, casambi_entry, "broadcast")
    assert "target_id" not in form["data_schema"].schema


async def test_tunable_white_prefills_gateway_defaults(
    hass: HomeAssistant, casambi_entry
) -> None:
    """New colour temperature forms start at the bridge defaults."""
    form = await _start(hass, casambi_entry, "tunable_white")
    defaults = {str(key): key.default() for key in form["data_schema"].schema}
    assert defaults["min_kelvin"] == 2700
    assert defaults["max_kelvin"] == 6500


async def test_dimmer_names_may_be_left_empty(
    hass: HomeAssistant, casambi_entry
) -> None:
    """An empty name field simply numbers the drivers."""
    result = await _add(
        hass,
        casambi_entry,
        "multi_dali",
        {
            "name": "Wohnzimmer",
            "target_id": 15,
            "dimmer_count": 3,
            "dimmer_names": "",
            "with_total_entity": True,
            "default_on_level": 255,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert _only_subentry(casambi_entry).data["dimmer_names"] == []


# ---------------------------------------------------------- duplicates --


async def test_duplicate_address_of_same_kind_is_rejected(
    hass: HomeAssistant, casambi_entry
) -> None:
    """A second element of the same kind on the same address is refused."""
    first = {"name": "Erste", "target_id": 19, "default_on_level": 255}
    await _add(hass, casambi_entry, "group", first)

    result = await _add(
        hass,
        casambi_entry,
        "group",
        {"name": "Zweite", "target_id": 19, "default_on_level": 255},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "duplicate_target"}
    assert len(casambi_entry.subentries) == 1


async def test_same_address_with_other_kind_is_allowed(
    hass: HomeAssistant, casambi_entry
) -> None:
    """Unit 2 and group 2 are different addresses in Casambi."""
    await _add(
        hass,
        casambi_entry,
        "simple",
        {
            "name": "Unit zwei",
            "target_id": 2,
            "default_on_level": 255,
            "optimistic_override": False,
        },
    )
    result = await _add(
        hass,
        casambi_entry,
        "group",
        {"name": "Gruppe zwei", "target_id": 2, "default_on_level": 255},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert len(casambi_entry.subentries) == 2


async def test_second_broadcast_is_rejected(hass: HomeAssistant, casambi_entry) -> None:
    """There is only one network, so only one broadcast element."""
    await _add(
        hass, casambi_entry, "broadcast", {"name": "Alle", "default_on_level": 255}
    )
    result = await _add(
        hass,
        casambi_entry,
        "broadcast",
        {"name": "Alle nochmal", "default_on_level": 255},
    )
    assert result["errors"] == {"base": "duplicate_target"}


# ---------------------------------------------------------- validation --


@pytest.mark.parametrize(
    ("step", "user_input", "expected_error"),
    [
        (
            "simple",
            {
                "name": "   ",
                "target_id": 19,
                "default_on_level": 255,
                "optimistic_override": False,
            },
            "name_required",
        ),
        (
            "tunable_white",
            {
                "name": "Bad",
                "target_id": 17,
                "min_kelvin": 6000,
                "max_kelvin": 3000,
                "default_on_level": 255,
                "optimistic_override": False,
            },
            "kelvin_range",
        ),
        (
            "multi_dali",
            {
                "name": "Gang",
                "target_id": 16,
                "dimmer_count": 2,
                "dimmer_names": "Eins\nZwei\nDrei",
                "with_total_entity": True,
                "default_on_level": 255,
            },
            "too_many_names",
        ),
    ],
)
async def test_validation_errors_map_to_their_keys(
    hass: HomeAssistant, casambi_entry, step, user_input, expected_error
) -> None:
    """Every validation rule surfaces the error key the translations define."""
    result = await _add(hass, casambi_entry, step, user_input)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected_error}
    assert not casambi_entry.subentries


@pytest.mark.parametrize("target_id", [0, 251])
async def test_target_id_outside_the_range_is_rejected(
    hass: HomeAssistant, casambi_entry, target_id
) -> None:
    """Addresses outside 1 to 250 never reach the model."""
    form = await _start(hass, casambi_entry, "simple")
    with pytest.raises(Exception) as excinfo:
        await hass.config_entries.subentries.async_configure(
            form["flow_id"],
            {
                "name": "X",
                "target_id": target_id,
                "default_on_level": 255,
                "optimistic_override": False,
            },
        )
    assert "target_id" in str(excinfo.value)


@pytest.mark.parametrize("dimmer_count", [0, 9])
async def test_dimmer_count_outside_the_range_is_rejected(
    hass: HomeAssistant, casambi_entry, dimmer_count
) -> None:
    """The gateway addresses at most eight drivers."""
    form = await _start(hass, casambi_entry, "multi_dali")
    with pytest.raises(Exception) as excinfo:
        await hass.config_entries.subentries.async_configure(
            form["flow_id"],
            {
                "name": "X",
                "target_id": 16,
                "dimmer_count": dimmer_count,
                "dimmer_names": "",
                "with_total_entity": True,
                "default_on_level": 255,
            },
        )
    assert "dimmer_count" in str(excinfo.value)


def test_dimmer_count_invalid_key_comes_from_the_model() -> None:
    """The model's own error still maps onto the translated key."""
    from custom_components.casambi_lithernet.models import ConfigurationError
    from custom_components.casambi_lithernet.subentry_flow import _error_key

    assert _error_key(ConfigurationError("dimmer_count out of range")) == (
        "dimmer_count_invalid"
    )
    assert _error_key(ConfigurationError("target_id out of range")) == (
        "target_id_invalid"
    )
    assert _error_key(ConfigurationError("something else entirely")) == "unknown"


# ------------------------------------------------------------- editing --


async def _reconfigure(hass: HomeAssistant, entry, subentry):
    return await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_UNIT),
        context={"source": "reconfigure", "subentry_id": subentry.subentry_id},
    )


async def test_edit_changes_values_but_not_kind_and_address(
    hass: HomeAssistant, casambi_entry
) -> None:
    """Editing keeps kind and address and updates everything else."""
    await _add(
        hass,
        casambi_entry,
        "tunable_white",
        {
            "name": "Badspot 1",
            "target_id": 20,
            "min_kelvin": 2700,
            "max_kelvin": 6500,
            "default_on_level": 255,
            "optimistic_override": False,
        },
    )
    subentry = _only_subentry(casambi_entry)

    form = await _reconfigure(hass, casambi_entry, subentry)
    assert form["type"] is FlowResultType.FORM
    assert form["step_id"] == "reconfigure"
    assert form["description_placeholders"] == {
        "kind": "tunable_white",
        "address": "20",
    }
    assert "target_id" not in form["data_schema"].schema
    assert "kind" not in form["data_schema"].schema

    defaults = {str(key): key.default() for key in form["data_schema"].schema}
    assert defaults["name"] == "Badspot 1"
    assert defaults["min_kelvin"] == 2700

    result = await hass.config_entries.subentries.async_configure(
        form["flow_id"],
        {
            "name": "Badspot eins",
            "min_kelvin": 2200,
            "max_kelvin": 6000,
            "default_on_level": 100,
            "optimistic_override": True,
        },
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"

    updated = _only_subentry(casambi_entry)
    assert updated.title == "Badspot eins"
    assert updated.data["kind"] == "tunable_white"
    assert updated.data["target_id"] == 20
    assert updated.data["name"] == "Badspot eins"
    assert updated.data["min_kelvin"] == 2200
    assert updated.data["max_kelvin"] == 6000
    assert updated.data["default_on_level"] == 100
    assert updated.data["optimistic_override"] is True


async def test_edit_multi_dali_changes_the_driver_count(
    hass: HomeAssistant, casambi_entry
) -> None:
    """Changing the number of drivers is allowed while editing."""
    await _add(
        hass,
        casambi_entry,
        "multi_dali",
        {
            "name": "Wohnzimmer",
            "target_id": 15,
            "dimmer_count": 3,
            "dimmer_names": "Linear direkt\nIndirekt 1\nIndirekt 2",
            "with_total_entity": True,
            "default_on_level": 255,
        },
    )
    subentry = _only_subentry(casambi_entry)
    form = await _reconfigure(hass, casambi_entry, subentry)
    defaults = {str(key): key.default() for key in form["data_schema"].schema}
    assert defaults["dimmer_names"] == "Linear direkt\nIndirekt 1\nIndirekt 2"

    result = await hass.config_entries.subentries.async_configure(
        form["flow_id"],
        {
            "name": "Wohnzimmer",
            "dimmer_count": 2,
            "dimmer_names": "Linear direkt\nIndirekt",
            "with_total_entity": False,
            "default_on_level": 255,
        },
    )
    assert result["type"] is FlowResultType.ABORT
    updated = _only_subentry(casambi_entry)
    assert updated.data["dimmer_count"] == 2
    assert updated.data["dimmer_names"] == ["Linear direkt", "Indirekt"]
    assert updated.data["with_total_entity"] is False
    assert updated.data["target_id"] == 15


async def test_edit_rejects_invalid_values(hass: HomeAssistant, casambi_entry) -> None:
    """A bad edit shows the error and leaves the stored element alone."""
    await _add(
        hass,
        casambi_entry,
        "simple",
        {
            "name": "Kochnische",
            "target_id": 12,
            "default_on_level": 255,
            "optimistic_override": False,
        },
    )
    subentry = _only_subentry(casambi_entry)
    form = await _reconfigure(hass, casambi_entry, subentry)
    result = await hass.config_entries.subentries.async_configure(
        form["flow_id"],
        {"name": "  ", "default_on_level": 255, "optimistic_override": False},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "name_required"}
    assert _only_subentry(casambi_entry).data["name"] == "Kochnische"


# ------------------------------------------------------------ removing --


async def test_element_can_be_removed(hass: HomeAssistant, casambi_entry) -> None:
    """Removing a subentry leaves the bridge entry loaded and empty."""
    await _add(
        hass,
        casambi_entry,
        "simple",
        {
            "name": "WC",
            "target_id": 9,
            "default_on_level": 255,
            "optimistic_override": False,
        },
    )
    subentry = _only_subentry(casambi_entry)
    assert hass.config_entries.async_remove_subentry(
        casambi_entry, subentry.subentry_id
    )
    await hass.async_block_till_done()
    assert not casambi_entry.subentries
    assert casambi_entry.state is ConfigEntryState.LOADED


# -------------------------------------------------------- translations --


def _flat_keys(obj: Any, prefix: str = "") -> set[str]:
    if isinstance(obj, dict):
        keys: set[str] = set()
        for key, value in obj.items():
            keys |= _flat_keys(value, f"{prefix}/{key}")
        return keys
    return {prefix}


def test_translation_files_have_identical_key_sets() -> None:
    """strings.json, en.json and de.json must stay in lockstep."""
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


def test_every_kind_has_a_form_step_and_a_menu_option() -> None:
    """No implemented kind may be missing from the translations."""
    strings = json.loads((COMPONENT_DIR / "strings.json").read_text(encoding="utf-8"))
    unit = strings["config_subentries"][SUBENTRY_TYPE_UNIT]
    for kind in IMPLEMENTED_KINDS:
        assert str(kind) in unit["step"]["user"]["menu_options"]
        assert str(kind) in unit["step"]
    assert "reconfigure" in unit["step"]
    assert set(unit["error"]) >= {
        "target_id_invalid",
        "duplicate_target",
        "name_required",
        "kelvin_range",
        "dimmer_count_invalid",
        "too_many_names",
        "unknown",
    }


def test_domain_and_kinds_are_what_the_flow_expects() -> None:
    """Guard the two contract values this module builds on."""
    assert DOMAIN == "casambi_lithernet"
    assert IMPLEMENTED_KINDS[0] is UnitKind.SIMPLE
