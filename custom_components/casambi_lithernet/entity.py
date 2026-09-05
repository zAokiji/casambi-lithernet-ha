"""Common entity base class for every Casambi platform.

Owned by package E, imported by the light platform (E), fan and switch (F),
the diagnostic entities (J) and scene/broadcast (K).

What the base class does, so a platform does not have to repeat it:

* **Device registration.** One Home Assistant device per Casambi element,
  linked to the gateway device with ``via_device``. Every entity built from the
  same :class:`~.models.UnitDefinition` lands on that one device, which is what
  makes the four drivers of unit 16 show up together.
* **Unique ids** come exclusively from the methods of ``UnitDefinition``
  (:meth:`~.models.UnitDefinition.base_unique_id`,
  :meth:`~.models.UnitDefinition.dimmer_unique_id`,
  :meth:`~.models.UnitDefinition.diagnostic_unique_id`), never from the display
  name, so renaming an element keeps its entities.
* **Subscription lifecycle.** Availability and, for unit addressed kinds, the
  ``propertys`` topic are subscribed in :meth:`async_added_to_hass` and removed
  again on teardown.
* **Two stage availability** (project document 6.6): the broker connection
  always counts, and elements that have ``propertys`` additionally need
  ``online``. Groups, scenes and broadcast only hang on the broker.
* **Last brightness memory** for turning on without a brightness, falling back
  to ``default_on_level`` before anything was ever set.
* **The three second fallback.** A non optimistic entity waits for the gateway
  to confirm what it sent; if nothing arrives within
  :data:`~.const.STATE_CONFIRM_TIMEOUT` it adopts the value it sent and writes
  a debug line. Optimistic entities set ``assumed_state`` and adopt the value
  immediately. An entity is optimistic when ``optimistic_override`` is ``True``,
  when the kind is optimistic by nature (``force_optimistic``), or when the
  gateway's polling method delivers no state at all; ``False`` and ``None`` in
  the override both mean "do not force it". See :func:`_decide_optimistic`.

How a subclass uses it:

1. Call ``super().__init__(gateway, definition, unique_id=..., ...)``.
2. Override :meth:`_read_initial_state` to adopt the gateway's last known
   state (retained messages make it available right away).
3. Override :meth:`_register_subscriptions` to subscribe to the topics the
   entity needs, registering every unsubscribe with ``self.async_on_remove``.
4. Call :meth:`_after_command` right after sending a command, with a callable
   that adopts the value that was sent.
5. Call :meth:`_state_confirmed` whenever a real state message arrives, so the
   fallback timer is dropped.
6. Override :meth:`_apply_properties` (calling ``super()``) to read more out of
   ``propertys`` than just ``online``; that is what package J does.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from homeassistant.core import CALLBACK_TYPE, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.event import async_call_later

from .const import DOMAIN, STATE_CONFIRM_TIMEOUT
from .contracts import CasambiGateway
from .models import UnitDefinition
from .state import UnitProperties, clamp_level

_LOGGER = logging.getLogger(__name__)


class CasambiEntity(Entity):
    """Base class for every entity of this integration.

    The entity is deliberately thin: it translates Home Assistant calls into a
    single gateway command and renders whatever the gateway last reported.
    """

    _attr_has_entity_name = True
    _attr_name: str | None = None
    _attr_should_poll = False

    def __init__(
        self,
        gateway: CasambiGateway,
        definition: UnitDefinition,
        *,
        unique_id: str,
        translation_key: str | None = None,
        force_optimistic: bool = False,
    ) -> None:
        """Set up device info, unique id and the optimistic decision.

        ``unique_id`` must come from one of the ``UnitDefinition`` id helpers.
        ``translation_key`` names the entity through ``entity.<platform>.<key>``
        in ``strings.json``; leave it out for the entity that represents the
        element itself, which then inherits the device name.
        ``force_optimistic`` is for entities the gateway can never confirm, such
        as one DALI driver inside a multi driver unit.
        """
        self._gateway = gateway
        self._definition = definition
        self._config = gateway.config
        self._attr_unique_id = unique_id
        if translation_key is not None:
            self._attr_translation_key = translation_key

        self._optimistic = _decide_optimistic(
            definition,
            delivers_state=self._config.delivers_state,
            force_optimistic=force_optimistic,
        )
        self._attr_assumed_state = self._optimistic

        self._broker_available = gateway.available
        self._online = True
        self._last_level: int | None = None
        self._pending_confirm: CALLBACK_TYPE | None = None
        self._pending_apply: Callable[[], None] | None = None

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, definition.base_unique_id(self._config.bridge_id))},
            name=definition.name,
            via_device=(DOMAIN, self._config.device_unique_id),
        )

    # ------------------------------------------------------------- naming --

    def _use_own_name(self, name: str) -> None:
        """Name this entity on its own instead of after its device.

        Used for the DALI drivers of a multi driver unit: their names already
        carry the room ("Wohnzimmer Linear direkt"), so prefixing the device
        name again would duplicate it and change the entity id.
        """
        self._attr_has_entity_name = False
        self._attr_name = name

    # ---------------------------------------------------------- lifecycle --

    async def async_added_to_hass(self) -> None:
        """Subscribe to availability, properties and the entity's own state."""
        self._broker_available = self._gateway.available
        self.async_on_remove(
            self._gateway.subscribe_availability(self._handle_availability)
        )

        if self._definition.has_properties:
            unit_id = self._definition.target_id
            if (properties := self._gateway.unit_properties(unit_id)) is not None:
                self._apply_properties(properties)
            self.async_on_remove(
                self._gateway.subscribe_unit_properties(
                    unit_id, self._handle_properties
                )
            )

        self._read_initial_state()
        self._register_subscriptions()

    async def async_will_remove_from_hass(self) -> None:
        """Drop a pending confirmation timer."""
        self._cancel_confirmation()

    @callback
    def _read_initial_state(self) -> None:
        """Adopt the gateway's last known state for this entity.

        Hook for subclasses, called once while the entity is being added and
        before :meth:`_register_subscriptions`. Retained messages mean this is
        usually enough to render right after a restart.
        """

    @callback
    def _register_subscriptions(self) -> None:
        """Subscribe to the state topics this entity needs.

        Hook for subclasses. Register every unsubscribe callable returned by
        the gateway with ``self.async_on_remove``.
        """

    # ------------------------------------------------------- availability --

    @property
    def available(self) -> bool:
        """Whether the broker and, where applicable, the unit are reachable."""
        if not self._broker_available:
            return False
        if self._definition.has_properties:
            return self._online
        return True

    @callback
    def _handle_availability(self, available: bool) -> None:
        """Follow the broker connection."""
        self._broker_available = available
        self.async_write_ha_state()

    @callback
    def _apply_properties(self, properties: UnitProperties) -> None:
        """Adopt ``propertys`` without writing the state.

        Subclasses that read more fields override this and call ``super()``.
        """
        self._online = properties.online

    @callback
    def _handle_properties(self, properties: UnitProperties) -> None:
        """Adopt an incoming ``propertys`` message."""
        self._apply_properties(properties)
        self.async_write_ha_state()

    # -------------------------------------------------------- command help --

    def _duration_ms(self, transition: float | None) -> int | None:
        """Turn a Home Assistant transition into a gateway duration.

        ``None`` means "no transition given", which makes the gateway use
        ``default_duration_ms`` from the entry configuration.
        """
        if transition is None:
            return None
        return max(0, round(float(transition) * 1000))

    def _resolve_on_level(self, requested: int | None) -> int:
        """Pick the level to switch on with.

        The requested brightness wins, then the last level this entity was set
        to, then ``default_on_level`` from the element definition.
        """
        if requested is not None:
            return max(1, clamp_level(requested))
        if self._last_level:
            return self._last_level
        return self._definition.default_on_level

    @callback
    def _remember_level(self, level: int) -> None:
        """Remember a level so switching on without a brightness reuses it."""
        if level > 0:
            self._last_level = clamp_level(level)

    # --------------------------------------------------- state confirmation --

    @callback
    def _after_command(self, apply: Callable[[], None]) -> None:
        """Handle the entity state after a command was sent.

        Optimistic entities adopt the value right away. Everybody else waits
        for the gateway and only adopts the value if no confirmation arrives
        within :data:`~.const.STATE_CONFIRM_TIMEOUT`.
        """
        self._cancel_confirmation()
        if self._optimistic:
            apply()
            self.async_write_ha_state()
            return
        self._pending_apply = apply
        self._pending_confirm = async_call_later(
            self.hass, STATE_CONFIRM_TIMEOUT, self._confirmation_timed_out
        )

    @callback
    def _confirmation_timed_out(self, _now: Any) -> None:
        """Adopt the value that was sent because the gateway stayed quiet."""
        self._pending_confirm = None
        apply = self._pending_apply
        self._pending_apply = None
        _LOGGER.debug(
            "%s: gateway did not confirm within %.0f s, adopting the value sent",
            self.entity_id,
            STATE_CONFIRM_TIMEOUT,
        )
        if apply is not None:
            apply()
        self.async_write_ha_state()

    @callback
    def _state_confirmed(self) -> None:
        """Note that real state arrived, so no fallback is needed."""
        self._cancel_confirmation()

    @callback
    def _cancel_confirmation(self) -> None:
        """Drop a pending fallback timer, if any."""
        if self._pending_confirm is not None:
            self._pending_confirm()
            self._pending_confirm = None
        self._pending_apply = None


def _decide_optimistic(
    definition: UnitDefinition, *, delivers_state: bool, force_optimistic: bool
) -> bool:
    """Whether an entity of this element runs without gateway confirmation.

    An entity is optimistic as soon as one of these holds:

    1. ``optimistic_override`` is ``True``. ``False`` and ``None`` mean the same
       thing, "do not force it": a subentry form check box can only produce
       ``True`` or ``False``, so an unticked box must not switch optimistic mode
       off again for a gateway that reports nothing.
    2. The entity is optimistic by its nature, which is what
       ``force_optimistic`` says. The single drivers of a multi driver unit are
       the case in version 0.1.
    3. The configured polling method delivers no state at all
       (:attr:`~.models.GatewayConfig.delivers_state` is ``False``, i.e. polling
       is ``inactive``); then every entity of that entry runs optimistically.
    """
    return (
        force_optimistic or definition.optimistic_override is True or not delivers_state
    )
