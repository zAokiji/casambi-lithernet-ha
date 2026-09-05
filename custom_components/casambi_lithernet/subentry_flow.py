"""Flows for adding, editing and removing Casambi elements.

**Placeholder created by package A, owned by package D from now on.**

Package D replaces this file with the per kind forms described in sections 6
and 7 of the project document. The stub can add a plain dimmable luminaire so
the rest of the integration is testable end to end.

What package D must keep:

* The class name :class:`UnitSubentryFlow`, because ``config_flow.py``
  imports it.
* Stored data has to be what
  :meth:`~.models.UnitDefinition.to_dict` produces, so the platforms can read
  it without knowing which form produced it.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigSubentryFlow, SubentryFlowResult

from .const import (
    CONF_NAME,
    CONF_TARGET_ID,
    UNIT_ID_MAX,
    UNIT_ID_MIN,
    UnitKind,
)
from .models import ConfigurationError, UnitDefinition

STEP_SIMPLE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME): str,
        vol.Required(CONF_TARGET_ID): vol.All(
            vol.Coerce(int), vol.Range(min=UNIT_ID_MIN, max=UNIT_ID_MAX)
        ),
    }
)


class UnitSubentryFlow(ConfigSubentryFlow):
    """Add one Casambi element to a bridge."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Ask for name and unit id, then store the element."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                definition = UnitDefinition.from_dict(
                    {
                        **user_input,
                        "kind": str(UnitKind.SIMPLE),
                    }
                )
            except ConfigurationError:
                errors["base"] = "target_id_invalid"
            else:
                return self.async_create_entry(
                    title=definition.name, data=definition.to_dict()
                )

        return self.async_show_form(
            step_id="user", data_schema=STEP_SIMPLE_SCHEMA, errors=errors
        )
