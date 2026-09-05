"""Guided setup of a gateway bridge, plus the options menu.

Implements the wizard from section 5 of the project
document:

1. ``user`` — prerequisites. MQTT has to be set up; the broker address and
   port are read from the MQTT config entry and shown, because that is exactly
   what has to be typed into the gateway a moment later.
2. ``gateway`` — gateway address and bridge ID. The bridge ID is the unique id
   of the config entry, so a second entry for the same bridge is refused.
3. ``polling`` — which polling method the user set in the gateway.
4. ``hints`` — text only: ``Use Broadcast`` off, fixed IP.
5. ``verify_command`` / ``verify_state`` — a blink test and a capture, both run
   against a gateway object that exists only for the duration of the check.
6. ``finish`` — create the entry.

Two rules the checks follow, because they run against a gateway that may still
be misconfigured:

* **A failing check never breaks the flow.** Errors from the blink test lead to
  the help text, not to a traceback.
* **The temporary gateway is always stopped**, on every exit including the user
  walking away, see :meth:`CasambiConfigFlow.async_remove`.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Final

import voluptuous as vol
from homeassistant.components.mqtt.const import CONF_BROKER
from homeassistant.components.mqtt.const import DOMAIN as MQTT_DOMAIN
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
)
from homeassistant.const import CONF_PORT
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_BRIDGE_ID,
    CONF_DEFAULT_DURATION_MS,
    CONF_DEFAULT_MAX_KELVIN,
    CONF_DEFAULT_MIN_KELVIN,
    CONF_GATEWAY_HOST,
    CONF_POLLING_METHOD,
    DEFAULT_BRIDGE_ID,
    DEFAULT_GATEWAY_HOST,
    DEFAULT_POLLING_METHOD,
    DOMAIN,
    KELVIN_FORM_MAX,
    KELVIN_FORM_MIN,
    SUBENTRY_TYPE_UNIT,
    UNIT_ID_MAX,
    UNIT_ID_MIN,
    PollingMethod,
)
from .contracts import CaptureResult, CasambiGateway
from .gateway import create_gateway
from .models import GatewayConfig
from .subentry_flow import UnitSubentryFlow

_LOGGER = logging.getLogger(__name__)

#: How long the state check listens. Patchable, so tests stay fast.
CAPTURE_SECONDS: Final = 20.0

#: How long the blink test leaves the luminaire at full brightness.
BLINK_SECONDS: Final = 2.0

#: Field of the blink test; only used inside the flow, never stored.
CONF_TEST_UNIT_ID: Final = "test_unit_id"

BRIDGE_ID_MIN: Final = 0
BRIDGE_ID_MAX: Final = 255

#: Fallbacks for the placeholders when the MQTT entry does not name them.
UNKNOWN_BROKER: Final = "?"
DEFAULT_MQTT_PORT: Final = 1883

#: Shown where a value was not seen at all; never a word, so it needs no
#: translation. The tick marks the opposite.
NOTHING: Final = "\u2013"
SEEN: Final = "\u2713"

DURATION_FORM_MAX: Final = 60000

_POLLING_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=[str(method) for method in PollingMethod],
        translation_key="polling_method",
        mode=SelectSelectorMode.LIST,
    )
)


# --------------------------------------------------------------- helpers ---


def _mqtt_broker(hass: HomeAssistant) -> tuple[str, int] | None:
    """Return broker address and port of the loaded MQTT entry, or None.

    None means the MQTT integration is not usable, which aborts the flow: the
    integration has no transport without it.
    """
    for entry in hass.config_entries.async_entries(MQTT_DOMAIN):
        if entry.state is not ConfigEntryState.LOADED:
            continue
        broker = str(entry.data.get(CONF_BROKER) or UNKNOWN_BROKER)
        port = int(entry.data.get(CONF_PORT) or DEFAULT_MQTT_PORT)
        return broker, port
    return None


@asynccontextmanager
async def async_temporary_gateway(
    hass: HomeAssistant, config: GatewayConfig
) -> AsyncIterator[CasambiGateway]:
    """Run a check against a gateway that only lives for that check.

    The config entry does not exist yet during setup, and during the options
    flow the entry's own gateway must not be disturbed, so both use a throwaway
    instance. It is stopped in every case, including an exception.
    """
    gateway = create_gateway(hass, config)
    await gateway.async_start()
    try:
        yield gateway
    finally:
        await gateway.async_stop()


def capture_placeholders(result: CaptureResult) -> dict[str, str]:
    """Turn a capture into the placeholders of the result texts.

    Every value goes into the translations as its own placeholder, so the
    sentence around it can be written in each language. Nothing here is a
    translatable word: numbers, addresses, topic kinds, and a tick or a dash
    for "was seen" and "was not seen".

    The topic kinds come from package B already normalised
    (``poll_device/<id>/values``) and are passed on verbatim; this function must
    not take them apart. Whether ``poll_devicet`` and ``poll_button`` arrived is
    reported separately, because the sensors and buttons of a later version
    depend on it. The reference installation saw 560 messages in 37 seconds and
    neither of the two.
    """
    return {
        "count": str(result.message_count),
        "seconds": f"{result.seconds:g}",
        "units": _ids(result.unit_ids),
        "groups": _ids(result.group_ids),
        "scenes": _ids(result.scene_ids),
        "topics": ", ".join(result.topic_kinds) if result.topic_kinds else NOTHING,
        "devicet": _seen(result.saw_device_elements),
        "buttons": _seen(result.saw_buttons),
    }


def broker_note(broker: str) -> str:
    """Warn when the broker address only resolves inside Home Assistant.

    With the Mosquitto add-on the MQTT integration connects to a hostname such
    as ``core-mosquitto``, which exists only inside Home Assistant. The Casambi
    gateway is a separate device on the network and cannot resolve it, so
    repeating that name in the setup text would send the user down a dead end.
    """
    if "." in broker or broker == UNKNOWN_BROKER:
        return ""
    return (
        f" `{broker}` ist ein Name, den nur Home Assistant selbst auflöst. "
        "Trage im Gateway stattdessen die IP-Adresse des Rechners ein, auf dem "
        "Home Assistant läuft."
    )


#: How many Casambi addresses the capture result lists before it counts the
#: rest. A gateway polls its whole address range, so the raw list runs to two
#: hundred entries and buries everything after it.
MAX_LISTED_IDS: Final = 12


def _ids(values: tuple[int, ...]) -> str:
    """Render a list of Casambi addresses, or a dash when there was none."""
    if not values:
        return NOTHING
    shown = ", ".join(str(value) for value in values[:MAX_LISTED_IDS])
    rest = len(values) - MAX_LISTED_IDS
    return shown if rest <= 0 else f"{shown} und {rest} weitere"


def _seen(value: bool) -> str:
    """Render a yes or no without using a word of any language."""
    return SEEN if value else NOTHING


def _settings_schema(config: GatewayConfig) -> vol.Schema:
    """Build the defaults form, prefilled from the current configuration."""
    return vol.Schema(
        {
            vol.Required(
                CONF_POLLING_METHOD, default=str(config.polling_method)
            ): _POLLING_SELECTOR,
            vol.Required(
                CONF_DEFAULT_DURATION_MS, default=config.default_duration_ms
            ): NumberSelector(
                NumberSelectorConfig(
                    min=0, max=DURATION_FORM_MAX, mode=NumberSelectorMode.BOX
                )
            ),
            vol.Required(
                CONF_DEFAULT_MIN_KELVIN, default=config.default_min_kelvin
            ): NumberSelector(
                NumberSelectorConfig(
                    min=KELVIN_FORM_MIN,
                    max=KELVIN_FORM_MAX,
                    mode=NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_DEFAULT_MAX_KELVIN, default=config.default_max_kelvin
            ): NumberSelector(
                NumberSelectorConfig(
                    min=KELVIN_FORM_MIN,
                    max=KELVIN_FORM_MAX,
                    mode=NumberSelectorMode.BOX,
                )
            ),
            vol.Required(CONF_GATEWAY_HOST, default=config.gateway_host): TextSelector(
                TextSelectorConfig(type=TextSelectorType.TEXT)
            ),
        }
    )


# ----------------------------------------------------------- config flow ---


class CasambiConfigFlow(ConfigFlow, domain=DOMAIN):
    """Walk the user through gateway, polling method and both checks."""

    VERSION = 1

    def __init__(self) -> None:
        """Start with nothing collected and no gateway running."""
        self._data: dict[str, Any] = {}
        self._broker: str = UNKNOWN_BROKER
        self._port: int = DEFAULT_MQTT_PORT
        self._test_unit_id: int | None = None
        self._capture: dict[str, str] = {}
        self._active_gateway: CasambiGateway | None = None

    @classmethod
    def async_get_supported_subentry_types(
        cls, config_entry: Any
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Offer the "add element" flow on the integration page."""
        return {SUBENTRY_TYPE_UNIT: UnitSubentryFlow}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Provide the options menu of an existing bridge."""
        return CasambiOptionsFlow()

    @callback
    def async_remove(self) -> None:
        """Stop a check that was still running when the flow went away.

        Home Assistant calls this synchronously, so the stop is scheduled. In
        practice the gateway of a check is already stopped, because
        :func:`async_temporary_gateway` never outlives its step; this is the
        belt for the case where a step is cancelled midway.
        """
        gateway = self._active_gateway
        self._active_gateway = None
        if gateway is not None:
            self.hass.async_create_task(gateway.async_stop())

    # ------------------------------------------------ step 1: prerequisites --

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Check that MQTT is there and show the broker the gateway needs."""
        broker = _mqtt_broker(self.hass)
        if broker is None:
            return self.async_abort(reason="mqtt_missing")
        self._broker, self._port = broker

        if user_input is None:
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema({}),
                description_placeholders=self._broker_placeholders(),
            )
        return await self.async_step_gateway()

    # -------------------------------------------- step 2: gateway and MQTT --

    async def async_step_gateway(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the gateway address and the bridge ID."""
        errors: dict[str, str] = {}
        if user_input is not None:
            bridge_id = int(user_input[CONF_BRIDGE_ID])
            host = str(user_input[CONF_GATEWAY_HOST]).strip()
            if not BRIDGE_ID_MIN <= bridge_id <= BRIDGE_ID_MAX:
                errors[CONF_BRIDGE_ID] = "bridge_id_invalid"
            else:
                await self.async_set_unique_id(f"{DOMAIN}_{bridge_id}")
                self._abort_if_unique_id_configured()
                self._data[CONF_BRIDGE_ID] = bridge_id
                self._data[CONF_GATEWAY_HOST] = host or DEFAULT_GATEWAY_HOST
                return await self.async_step_polling()

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_GATEWAY_HOST,
                    default=self._data.get(CONF_GATEWAY_HOST, DEFAULT_GATEWAY_HOST),
                ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
                vol.Required(
                    CONF_BRIDGE_ID,
                    default=self._data.get(CONF_BRIDGE_ID, DEFAULT_BRIDGE_ID),
                ): NumberSelector(
                    # No maximum here on purpose: an out of range value has to
                    # reach the handler so it can show ``bridge_id_invalid``.
                    NumberSelectorConfig(
                        min=BRIDGE_ID_MIN, step=1, mode=NumberSelectorMode.BOX
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="gateway",
            data_schema=schema,
            errors=errors,
            description_placeholders=self._broker_placeholders(),
        )

    # ------------------------------------------- step 3: polling method ----

    async def async_step_polling(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Record which polling method the gateway is set to."""
        if user_input is not None:
            self._data[CONF_POLLING_METHOD] = str(user_input[CONF_POLLING_METHOD])
            return await self.async_step_hints()

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_POLLING_METHOD,
                    default=self._data.get(
                        CONF_POLLING_METHOD, str(DEFAULT_POLLING_METHOD)
                    ),
                ): _POLLING_SELECTOR
            }
        )
        return self.async_show_form(step_id="polling", data_schema=schema)

    # ------------------------------------------------------ step 4: hints --

    async def async_step_hints(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the two remaining gateway settings; no input needed."""
        if user_input is None:
            return self.async_show_form(step_id="hints", data_schema=vol.Schema({}))
        return await self.async_step_verify_command()

    # --------------------------------------- step 5a: do commands arrive? --

    async def async_step_verify_command(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a unit ID and blink it."""
        errors: dict[str, str] = {}
        if user_input is not None:
            unit_id = int(user_input[CONF_TEST_UNIT_ID])
            if not UNIT_ID_MIN <= unit_id <= UNIT_ID_MAX:
                errors[CONF_TEST_UNIT_ID] = "unit_id_invalid"
            else:
                self._test_unit_id = unit_id
                return await self._async_blink()

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_TEST_UNIT_ID,
                    default=self._test_unit_id or UNIT_ID_MIN,
                ): NumberSelector(
                    # See the bridge ID above: the range is checked in the
                    # handler so the user gets ``unit_id_invalid``.
                    NumberSelectorConfig(
                        min=UNIT_ID_MIN, step=1, mode=NumberSelectorMode.BOX
                    )
                )
            }
        )
        return self.async_show_form(
            step_id="verify_command", data_schema=schema, errors=errors
        )

    async def _async_blink(self) -> ConfigFlowResult:
        """Run the blink test and ask what the user saw.

        A gateway that is still misconfigured must not break the setup, so any
        error leads straight to the help text.
        """
        unit_id = self._test_unit_id or UNIT_ID_MIN
        try:
            async with async_temporary_gateway(self.hass, self._config()) as gateway:
                self._active_gateway = gateway
                await gateway.async_blink_test(unit_id, BLINK_SECONDS)
        except Exception:  # the gateway may not be reachable yet
            _LOGGER.exception("Blink test for unit %s failed", unit_id)
            return await self.async_step_blink_no()
        finally:
            self._active_gateway = None

        return await self.async_step_verify_blink()

    async def async_step_verify_blink(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask whether the luminaire reacted."""
        return self.async_show_menu(
            step_id="verify_blink",
            menu_options=["blink_yes", "blink_repeat", "blink_no"],
            description_placeholders={
                "unit_id": str(self._test_unit_id or UNIT_ID_MIN)
            },
        )

    async def async_step_blink_yes(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Commands arrive; move on to the state check."""
        return await self.async_step_verify_state()

    async def async_step_blink_repeat(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Blink the same unit once more."""
        return await self._async_blink()

    async def async_step_blink_no(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the three things to check, then try again or skip."""
        return self.async_show_menu(
            step_id="blink_no",
            menu_options=["verify_command", "verify_state"],
            description_placeholders={
                "broker": self._broker,
                "bridge_id": str(self._data.get(CONF_BRIDGE_ID, DEFAULT_BRIDGE_ID)),
            },
        )

    # ------------------------------------ step 5b: does state come back? ---

    async def async_step_verify_state(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Listen to the gateway and report what arrived.

        With polling method ``inactive`` the gateway sends nothing by design,
        so there is nothing to listen for (docs/DESIGN.md): the
        check is skipped and the consequence explained instead. The guard sits
        here rather than at the call sites, so every way into the state check
        passes it.
        """
        if self._polling_is_inactive():
            return await self.async_step_polling_inactive()

        if user_input is None:
            return self.async_show_form(
                step_id="verify_state",
                data_schema=vol.Schema({}),
                description_placeholders={"seconds": f"{CAPTURE_SECONDS:g}"},
            )

        result = await self._async_capture()
        self._capture = capture_placeholders(result)
        if not result.saw_any_state:
            return await self.async_step_no_state()
        return await self.async_step_verify_state_result()

    async def async_step_verify_state_result(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show what the capture saw and let the user finish or listen again."""
        return self.async_show_menu(
            step_id="verify_state_result",
            menu_options=["finish", "verify_state"],
            description_placeholders=self._capture,
        )

    async def async_step_no_state(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Nothing arrived: point at the polling method, offer both ways on."""
        return self.async_show_menu(
            step_id="no_state",
            menu_options=["verify_state", "finish"],
            description_placeholders=self._capture,
        )

    async def async_step_polling_inactive(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Explain what "inactive" costs and offer to change it."""
        return self.async_show_menu(
            step_id="polling_inactive", menu_options=["finish", "polling"]
        )

    async def _async_capture(self) -> CaptureResult:
        """Capture once, never letting a failure end the flow."""
        try:
            async with async_temporary_gateway(self.hass, self._config()) as gateway:
                self._active_gateway = gateway
                return await gateway.async_capture(CAPTURE_SECONDS)
        except Exception:  # a broken capture is a result, not a crash
            _LOGGER.exception("State capture failed")
            return CaptureResult(seconds=CAPTURE_SECONDS, message_count=0)
        finally:
            self._active_gateway = None

    # ----------------------------------------------------- step 6: finish --

    async def async_step_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create the config entry for this bridge."""
        config = self._config()
        return self.async_create_entry(
            title=f"Casambi Bridge {config.bridge_id} ({config.gateway_host})",
            data=config.to_dict(),
        )

    # ---------------------------------------------------------- internals --

    def _config(self) -> GatewayConfig:
        """Build the configuration from what has been collected so far."""
        return GatewayConfig.from_dict(self._data)

    def _polling_is_inactive(self) -> bool:
        """Whether the user said the gateway reports no state at all."""
        return self._config().polling_method is PollingMethod.INACTIVE

    def _broker_placeholders(self) -> dict[str, str]:
        """Broker address and port, used by several steps."""
        return {
            "broker": self._broker,
            "port": str(self._port),
            "broker_note": broker_note(self._broker),
        }


# ---------------------------------------------------------- options flow ---


class CasambiOptionsFlow(OptionsFlow):
    """Change the defaults of a bridge or re-run the state check."""

    def __init__(self) -> None:
        """Start without a capture result."""
        self._capture: dict[str, str] = {}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer defaults and the connection check."""
        return self.async_show_menu(step_id="init", menu_options=["settings", "verify"])

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit polling method, transition time, kelvin bounds and address."""
        if user_input is not None:
            return self.async_create_entry(
                data={
                    **self.config_entry.options,
                    CONF_POLLING_METHOD: str(user_input[CONF_POLLING_METHOD]),
                    CONF_DEFAULT_DURATION_MS: int(user_input[CONF_DEFAULT_DURATION_MS]),
                    CONF_DEFAULT_MIN_KELVIN: int(user_input[CONF_DEFAULT_MIN_KELVIN]),
                    CONF_DEFAULT_MAX_KELVIN: int(user_input[CONF_DEFAULT_MAX_KELVIN]),
                    CONF_GATEWAY_HOST: str(user_input[CONF_GATEWAY_HOST]).strip()
                    or DEFAULT_GATEWAY_HOST,
                }
            )
        return self.async_show_form(
            step_id="settings", data_schema=_settings_schema(self._config())
        )

    async def async_step_verify(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Run the same state check the setup wizard uses."""
        if user_input is None:
            return self.async_show_form(
                step_id="verify",
                data_schema=vol.Schema({}),
                description_placeholders={"seconds": f"{CAPTURE_SECONDS:g}"},
            )

        try:
            async with async_temporary_gateway(self.hass, self._config()) as gateway:
                result = await gateway.async_capture(CAPTURE_SECONDS)
        except Exception:  # a broken capture is a result, not a crash
            _LOGGER.exception("State capture failed")
            result = CaptureResult(seconds=CAPTURE_SECONDS, message_count=0)

        self._capture = capture_placeholders(result)
        return await self.async_step_verify_result()

    async def async_step_verify_result(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the capture summary and close the options flow."""
        if user_input is None:
            return self.async_show_form(
                step_id="verify_result",
                data_schema=vol.Schema({}),
                description_placeholders=self._capture,
            )
        return self.async_create_entry(data=dict(self.config_entry.options))

    def _config(self) -> GatewayConfig:
        """Read the entry's configuration, options winning over data."""
        return GatewayConfig.from_dict(
            {**self.config_entry.data, **self.config_entry.options}
        )
