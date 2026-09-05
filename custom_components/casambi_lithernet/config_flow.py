"""Config and options flow for the gateway.

**Placeholder created by package A, owned by package C from now on.**

Package C replaces this file with the guided setup described in section 5 of
the project document: prerequisites, gateway and MQTT, polling method, hints,
blink test, state capture. The stub creates an entry with defaults so the
integration is installable while the packages are built in parallel.

Two things package C must keep, because other parts depend on them:

* :meth:`CasambiConfigFlow.async_get_supported_subentry_types` wires in the
  element flows owned by package D.
* The entry data has to be what
  :meth:`~.models.GatewayConfig.to_dict` produces.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
)

from .const import (
    CONF_BRIDGE_ID,
    CONF_GATEWAY_HOST,
    DEFAULT_BRIDGE_ID,
    DEFAULT_GATEWAY_HOST,
    DOMAIN,
    SUBENTRY_TYPE_UNIT,
)
from .models import GatewayConfig
from .subentry_flow import UnitSubentryFlow

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_GATEWAY_HOST, default=DEFAULT_GATEWAY_HOST): str,
        vol.Required(CONF_BRIDGE_ID, default=DEFAULT_BRIDGE_ID): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=255)
        ),
    }
)


class CasambiConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up one gateway bridge."""

    VERSION = 1

    @classmethod
    def async_get_supported_subentry_types(
        cls, config_entry: Any
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Offer the "add element" flow on the integration page."""
        return {SUBENTRY_TYPE_UNIT: UnitSubentryFlow}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the bridge and create the entry."""
        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=STEP_USER_SCHEMA)

        bridge_id = int(user_input[CONF_BRIDGE_ID])
        await self.async_set_unique_id(f"{DOMAIN}_{bridge_id}")
        self._abort_if_unique_id_configured()

        config = GatewayConfig(
            bridge_id=bridge_id,
            gateway_host=str(user_input[CONF_GATEWAY_HOST]),
        )
        return self.async_create_entry(
            title=f"Casambi Bridge {bridge_id}",
            data=config.to_dict(),
        )
