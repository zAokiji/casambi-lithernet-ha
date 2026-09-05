"""Package A smoke tests: the integration loads and the contracts hold."""

from __future__ import annotations

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from custom_components.casambi_lithernet.const import TargetType, UnitKind
from custom_components.casambi_lithernet.models import (
    ConfigurationError,
    GatewayConfig,
    UnitDefinition,
)
from custom_components.casambi_lithernet.state import kelvin_to_tc, tc_to_kelvin


async def test_entry_sets_up(hass: HomeAssistant, casambi_entry) -> None:
    """A bare bridge entry loads and unloads again."""
    assert casambi_entry.state is ConfigEntryState.LOADED
    assert await hass.config_entries.async_unload(casambi_entry.entry_id)


def test_gateway_config_roundtrip() -> None:
    """Stored data survives a round trip unchanged."""
    config = GatewayConfig(bridge_id=3)
    assert GatewayConfig.from_dict(config.to_dict()) == config


def test_unit_definition_roundtrip() -> None:
    """A unit definition survives a round trip unchanged."""
    unit = UnitDefinition(kind=UnitKind.SIMPLE, name="Esstisch", target_id=18)
    assert UnitDefinition.from_dict(unit.to_dict()) == unit


@pytest.mark.parametrize(
    ("kind", "target_id", "expected"),
    [
        (UnitKind.SIMPLE, 19, "casambi_lithernet_0_u19"),
        (UnitKind.GROUP, 2, "casambi_lithernet_0_g2"),
        (UnitKind.SCENE, 2, "casambi_lithernet_0_s2"),
        (UnitKind.BROADCAST, 0, "casambi_lithernet_0_broadcast"),
    ],
)
def test_unique_ids_separate_address_spaces(kind, target_id, expected) -> None:
    """A unit and a group with the same number never collide."""
    unit = UnitDefinition(kind=kind, name="X", target_id=target_id)
    assert unit.base_unique_id(0) == expected


def test_invalid_target_id_is_rejected() -> None:
    """Addresses outside the Casambi range do not become entities."""
    with pytest.raises(ConfigurationError):
        UnitDefinition(kind=UnitKind.SIMPLE, name="X", target_id=999).validate()


@pytest.mark.parametrize(("kelvin", "expected"), [(2700, 0), (6500, 255), (4600, 128)])
def test_kelvin_conversion_hits_the_edges(kelvin, expected) -> None:
    """The normalised scale reaches both ends exactly."""
    assert kelvin_to_tc(kelvin, 2700, 6500) == expected


def test_kelvin_conversion_is_reversible() -> None:
    """Converting back lands within one step."""
    for kelvin in (2700, 3500, 5000, 6500):
        back = tc_to_kelvin(kelvin_to_tc(kelvin, 2700, 6500), 2700, 6500)
        assert abs(back - kelvin) <= 15


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        (UnitKind.SIMPLE, TargetType.UNIT),
        (UnitKind.TUNABLE_WHITE, TargetType.UNIT),
        (UnitKind.MULTI_DALI, TargetType.UNIT),
        (UnitKind.SWITCH, TargetType.UNIT),
        (UnitKind.GROUP, TargetType.GROUP),
        (UnitKind.SCENE, TargetType.SCENE_ALL),
        (UnitKind.BROADCAST, TargetType.BROADCAST),
    ],
)
def test_target_type_per_kind(kind, expected) -> None:
    """Every kind addresses the gateway through the right target type.

    The platforms read this instead of hardcoding a value, so a wrong entry
    here would send commands to the wrong address space.
    """
    target_id = 0 if kind is UnitKind.BROADCAST else 1
    unit = UnitDefinition(kind=kind, name="X", target_id=target_id)
    assert unit.target_type is expected
