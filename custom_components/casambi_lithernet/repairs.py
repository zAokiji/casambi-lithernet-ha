"""Repair issues of the Casambi (Lithernet MQTT) integration.

Two issues from docs/DESIGN.md, "Fehlerbehandlung", both
purely informational: the user has to change something outside Home Assistant,
so there is nothing a fix flow could do on its own.

* ``mqtt_missing`` — the MQTT integration is not available, so the gateway
  entry cannot load. Raised where the entry gives up, cleared as soon as MQTT
  answers again.
* ``no_state_received`` — nothing at all arrived from the gateway for
  :data:`~.const.NO_STATE_REPAIR_AFTER` seconds although the configured polling
  method should deliver state. With polling ``inactive`` silence is exactly
  what the user asked for, so the issue is never raised in that case. It
  disappears again with the first message.

Both helpers are per config entry, so a second bridge raises its own issue and
unloading one entry never clears the other one's.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from homeassistant.components import mqtt
from homeassistant.components.repairs import ConfirmRepairFlow
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import DOMAIN, NO_STATE_REPAIR_AFTER
from .contracts import CasambiGateway

_LOGGER = logging.getLogger(__name__)

#: Translation keys below ``issues`` in ``strings.json``; also the prefix of
#: the issue ids, which carry the entry id so bridges stay independent.
ISSUE_MQTT_MISSING = "mqtt_missing"
ISSUE_NO_STATE_RECEIVED = "no_state_received"

#: How often the watchdog looks whether anything arrived at all. Short enough
#: that the repair disappears soon after the first message, long enough to cost
#: nothing over the day it has to wait.
STATE_CHECK_INTERVAL = timedelta(minutes=15)


def build_issue_id(kind: str, entry: ConfigEntry) -> str:
    """Build the issue id of one kind of problem for one config entry."""
    return f"{kind}_{entry.entry_id}"


# ------------------------------------------------------------ MQTT missing --


@callback
def async_report_mqtt_missing(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Tell the user that the entry cannot load without the MQTT integration."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        build_issue_id(ISSUE_MQTT_MISSING, entry),
        is_fixable=False,
        issue_domain=DOMAIN,
        severity=ir.IssueSeverity.ERROR,
        translation_key=ISSUE_MQTT_MISSING,
    )


@callback
def async_clear_mqtt_missing(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Withdraw the MQTT issue because the transport is there again."""
    ir.async_delete_issue(hass, DOMAIN, build_issue_id(ISSUE_MQTT_MISSING, entry))


async def async_check_mqtt(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Wait for the MQTT client and keep the repair in sync with the answer.

    Returns whether MQTT can be used. The caller raises ``ConfigEntryNotReady``
    when it cannot; the repair explains to the user why the entry stays grey.
    """
    if await mqtt.async_wait_for_mqtt_client(hass):
        async_clear_mqtt_missing(hass, entry)
        return True
    _LOGGER.debug("MQTT is not available, raising the %s issue", ISSUE_MQTT_MISSING)
    async_report_mqtt_missing(hass, entry)
    return False


# --------------------------------------------------------- no state at all --


@callback
def async_clear_no_state(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Withdraw the "check the polling method" issue."""
    ir.async_delete_issue(hass, DOMAIN, build_issue_id(ISSUE_NO_STATE_RECEIVED, entry))


@callback
def async_watch_for_state(
    hass: HomeAssistant, entry: ConfigEntry, gateway: CasambiGateway
) -> CALLBACK_TYPE:
    """Watch whether the gateway ever reports anything.

    Returns a callable that stops watching and withdraws the issue; register it
    with ``entry.async_on_unload``.

    With polling ``inactive`` the gateway is not supposed to report anything,
    so nothing is watched and no issue can ever appear.
    """
    async_clear_no_state(hass, entry)
    if not gateway.config.delivers_state:
        _LOGGER.debug(
            "Polling is inactive on bridge %s, not watching for state",
            gateway.config.bridge_id,
        )
        return lambda: None
    return _NoStateWatchdog(hass, entry, gateway).async_start()


class _NoStateWatchdog:
    """Raises ``no_state_received`` after a day without a single message.

    The gateway's message counter only ever grows, so once something arrived
    the issue can never become true again and the watchdog stops itself.
    """

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, gateway: CasambiGateway
    ) -> None:
        """Start the clock at the moment the entry was set up."""
        self._hass = hass
        self._entry = entry
        self._gateway = gateway
        self._deadline = dt_util.utcnow() + timedelta(seconds=NO_STATE_REPAIR_AFTER)
        self._cancel_interval: CALLBACK_TYPE | None = None

    @callback
    def async_start(self) -> CALLBACK_TYPE:
        """Begin checking and return the callable that stops it again."""
        self._cancel_interval = async_track_time_interval(
            self._hass, self._async_check, STATE_CHECK_INTERVAL
        )
        return self.async_stop

    @callback
    def async_stop(self) -> None:
        """Stop checking and withdraw the issue."""
        if self._cancel_interval is not None:
            self._cancel_interval()
            self._cancel_interval = None
        async_clear_no_state(self._hass, self._entry)

    @callback
    def _async_check(self, now: datetime) -> None:
        """Raise the issue once the deadline passed without any message."""
        diagnostics = self._gateway.diagnostics()
        if diagnostics.messages_received > 0:
            self.async_stop()
            return
        if now < self._deadline:
            return
        if not diagnostics.subscribed_topics:
            # Nobody is listening, because no element is configured yet. The
            # gateway may well be talking; we simply cannot tell, and telling
            # the user to check the polling method would be wrong.
            return
        _LOGGER.debug(
            "Bridge %s has not reported anything since setup, raising %s",
            self._gateway.config.bridge_id,
            ISSUE_NO_STATE_RECEIVED,
        )
        ir.async_create_issue(
            self._hass,
            DOMAIN,
            build_issue_id(ISSUE_NO_STATE_RECEIVED, self._entry),
            is_fixable=False,
            issue_domain=DOMAIN,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_NO_STATE_RECEIVED,
        )


# ------------------------------------------------------------- fix flows ----


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> ConfirmRepairFlow:
    """Serve the repairs platform.

    Both issues are informational and created with ``is_fixable=False``, so
    this is never reached in practice. It exists because Home Assistant refuses
    a ``repairs`` platform without it.
    """
    return ConfirmRepairFlow()


@callback
def async_clear_issues(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Withdraw every issue this entry raised.

    Issue ids carry the entry id, so an entry that gets deleted while one of
    its issues stands would leave that issue in the repairs panel forever:
    neither issue is fixable from the panel, and nothing would ever raise the
    same id again.
    """
    async_clear_mqtt_missing(hass, entry)
    async_clear_no_state(hass, entry)
