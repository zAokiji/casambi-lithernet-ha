"""The gateway contract, checked against both implementations.

Eleven test modules replace the real gateway with
:class:`~fake_gateway.FakeGateway`; if the double drifts away from
:class:`~custom_components.casambi_lithernet.gateway.MqttCasambiGateway`, all of
them keep passing while saying nothing about the real integration. Nothing used
to cover the double itself.

Every test in this module therefore runs twice, once against the real gateway
with a mocked broker and once against the fake, through the small
:class:`Harness` adapter that hides the only honest difference between them:
how a message gets in. The real one is fed an MQTT payload, the fake is handed
the parsed state object, because it deliberately has no parser.

The promises being checked are the ones in ``contracts.py``:

* a subscription delivers an incoming state to every listener on that topic,
* unsubscribing removes that listener and only that one,
* the last known state is readable afterwards, which is what lets an entity
  added later render at once,
* ``available`` reports the broker connection it was given,
* a listener that raises does not stop the others; entities must not be able
  to take each other down.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fake_gateway import FakeGateway, unit_values_from
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_fire_mqtt_message

from custom_components.casambi_lithernet import gateway as gateway_module
from custom_components.casambi_lithernet.contracts import CasambiGateway
from custom_components.casambi_lithernet.models import GatewayConfig
from custom_components.casambi_lithernet.state import UnitValues

UNIT = 19

#: A values payload in the shape the installation actually publishes.
VALUES: dict[str, Any] = {
    "scene": 0,
    "level": 128,
    "last_level": 128,
    "cct_level": 0,
    "red": 254,
    "green": 0,
    "blue": 1,
    "white": 254,
    "hue": 0,
    "sat": 0,
    "x": 0,
    "y": 0,
    "level_xy": 0,
    "vertical": 0,
    "last_change": 54789,
}


class Harness:
    """One gateway plus the two things a test needs to drive it.

    Everything else in a test goes through the contract itself, so the same
    assertions run against both implementations.
    """

    def __init__(self, hass: HomeAssistant, gateway: CasambiGateway) -> None:
        """Remember the gateway under test."""
        self.hass = hass
        self.gateway = gateway

    async def deliver(self, unit_id: int, values: dict[str, Any]) -> None:
        """Make a values message arrive for a unit."""
        raise NotImplementedError

    def set_available(self, available: bool) -> None:
        """Report the broker as connected or gone."""
        raise NotImplementedError

    async def settle(self) -> None:
        """Let Home Assistant finish whatever the gateway started."""
        await self.hass.async_block_till_done()


class RealHarness(Harness):
    """The real gateway; a message arrives as an MQTT payload."""

    def __init__(
        self, hass: HomeAssistant, gateway: CasambiGateway, monkeypatch: Any
    ) -> None:
        """Keep the monkeypatch around for the broker connection."""
        super().__init__(hass, gateway)
        self._monkeypatch = monkeypatch

    async def deliver(self, unit_id: int, values: dict[str, Any]) -> None:
        """Publish the payload the gateway would receive from the broker."""
        async_fire_mqtt_message(
            self.hass,
            f"casambi/0/get/poll_device/{unit_id}/values",
            json.dumps(values),
        )
        await self.hass.async_block_till_done()

    def set_available(self, available: bool) -> None:
        """Stand in for the MQTT integration's connection state."""
        self._monkeypatch.setattr(
            gateway_module.mqtt, "is_connected", lambda _hass: available
        )


class FakeHarness(Harness):
    """The test double; a message arrives as an already parsed state object."""

    async def deliver(self, unit_id: int, values: dict[str, Any]) -> None:
        """Push the state object the real parser would have produced."""
        assert isinstance(self.gateway, FakeGateway)
        self.gateway.push_unit_values(unit_id, unit_values_from(json.dumps(values)))
        await self.hass.async_block_till_done()

    def set_available(self, available: bool) -> None:
        """Set the fake broker connection."""
        assert isinstance(self.gateway, FakeGateway)
        self.gateway.set_available(available)


@pytest.fixture(params=["real", "fake"])
async def harness(
    request, hass: HomeAssistant, mqtt_mock, monkeypatch
) -> AsyncIterator[Harness]:
    """Provide the same started gateway twice: once real, once faked."""
    config = GatewayConfig()
    if request.param == "real":
        real = gateway_module.create_gateway(hass, config)
        await real.async_start()
        yield RealHarness(hass, real, monkeypatch)
        await real.async_stop()
        return
    fake = FakeGateway(config)
    await fake.async_start()
    yield FakeHarness(hass, fake)
    await fake.async_stop()


async def subscribe(harness: Harness, listener: Any, unit_id: int = UNIT) -> Any:
    """Subscribe a listener and wait until the subscription is in place."""
    remove = harness.gateway.subscribe_unit(unit_id, listener)
    await harness.settle()
    return remove


# ------------------------------------------------------------ delivery ---


async def test_a_subscription_reaches_every_listener(harness: Harness) -> None:
    """Both entities watching a unit see the same incoming state."""
    first: list[UnitValues] = []
    second: list[UnitValues] = []
    await subscribe(harness, first.append)
    await subscribe(harness, second.append)

    await harness.deliver(UNIT, VALUES)

    assert [values.level for values in first] == [128]
    assert [values.level for values in second] == [128]


async def test_a_listener_of_another_unit_is_left_alone(harness: Harness) -> None:
    """A message for unit 19 never reaches the entity watching unit 12."""
    mine: list[UnitValues] = []
    other: list[UnitValues] = []
    await subscribe(harness, mine.append, UNIT)
    await subscribe(harness, other.append, 12)

    await harness.deliver(UNIT, VALUES)

    assert len(mine) == 1
    assert other == []


async def test_unsubscribing_stops_only_that_listener(harness: Harness) -> None:
    """Removing one entity leaves the other one subscribed."""
    going: list[UnitValues] = []
    staying: list[UnitValues] = []
    remove = await subscribe(harness, going.append)
    await subscribe(harness, staying.append)

    remove()
    await harness.deliver(UNIT, VALUES)

    assert going == []
    assert len(staying) == 1


async def test_the_last_listener_can_leave_and_come_back(harness: Harness) -> None:
    """After the last unsubscribe a new subscription works again."""
    first: list[UnitValues] = []
    remove = await subscribe(harness, first.append)
    remove()
    await harness.settle()

    second: list[UnitValues] = []
    await subscribe(harness, second.append)
    await harness.deliver(UNIT, VALUES)

    assert first == []
    assert len(second) == 1


# ---------------------------------------------------- last known state ---


async def test_the_last_known_state_is_readable(harness: Harness) -> None:
    """What arrived is kept, so an entity added later renders at once."""
    assert harness.gateway.unit_values(UNIT) is None
    await subscribe(harness, lambda _values: None)

    await harness.deliver(UNIT, VALUES)

    values = harness.gateway.unit_values(UNIT)
    assert values is not None
    assert values.level == 128
    assert values.last_level == 128
    # And it is the same for a subscriber that only arrives now.
    late: list[UnitValues] = []
    await subscribe(harness, late.append)
    assert harness.gateway.unit_values(UNIT) == values
    assert harness.gateway.unit_values(12) is None


# ----------------------------------------------------------- available ---


async def test_available_follows_the_broker(harness: Harness) -> None:
    """``available`` reports the connection state it was given."""
    harness.set_available(True)
    assert harness.gateway.available is True

    harness.set_available(False)
    assert harness.gateway.available is False

    harness.set_available(True)
    assert harness.gateway.available is True


# ------------------------------------------------------ a bad listener ---


async def test_a_raising_listener_does_not_stop_the_others(
    harness: Harness, caplog
) -> None:
    """One broken entity must not keep the others from being updated.

    ``contracts.py`` promises that callbacks never raise, but an entity that
    breaks anyway would otherwise silently freeze every other entity on that
    unit, which is the hardest kind of fault to find in a running house.
    """
    seen: list[UnitValues] = []

    def _explode(_values: UnitValues) -> None:
        raise RuntimeError("this entity is broken")

    await subscribe(harness, _explode)
    await subscribe(harness, seen.append)

    await harness.deliver(UNIT, VALUES)

    assert [values.level for values in seen] == [128]
    assert "this entity is broken" in caplog.text

    # And the subscription still works for the next message.
    await harness.deliver(UNIT, {**VALUES, "level": 10})
    assert [values.level for values in seen] == [128, 10]
