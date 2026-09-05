"""Flows for adding, editing and removing Casambi elements.

The flows never talk to MQTT; they only produce and read
back what :meth:`~.models.UnitDefinition.to_dict` stores in a config subentry.

Adding starts with a menu of the kinds implemented in version 0.1 (see
``IMPLEMENTED_KINDS``). Each kind has its own form step whose id equals the
kind, so the translations under ``config_subentries.unit.step.<kind>`` apply
without any indirection.

Editing reuses the same fields in a single ``reconfigure`` step. Kind and
address stay fixed: changing them would change the unique id and therefore
orphan the existing entities, so they are shown as a hint in the description
instead of being offered as fields.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any, Final

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigSubentry,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.helpers.selector import (
    BooleanSelector,
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
    CONF_DEFAULT_ON_LEVEL,
    CONF_DIMMER_COUNT,
    CONF_DIMMER_NAMES,
    CONF_KIND,
    CONF_MAX_KELVIN,
    CONF_MIN_KELVIN,
    CONF_NAME,
    CONF_OPTIMISTIC_OVERRIDE,
    CONF_SWITCH_DOMAIN,
    CONF_TARGET_ID,
    CONF_WITH_TOTAL_ENTITY,
    DEFAULT_ON_LEVEL,
    GROUP_ID_MAX,
    GROUP_ID_MIN,
    IMPLEMENTED_KINDS,
    KELVIN_FORM_MAX,
    KELVIN_FORM_MIN,
    LEVEL_MAX,
    MAX_DIMMER_COUNT,
    SCENE_ID_MAX,
    SCENE_ID_MIN,
    SUBENTRY_TYPE_UNIT,
    SWITCH_DOMAIN_FAN,
    SWITCH_DOMAIN_SWITCH,
    TARGETLESS_KINDS,
    UNIT_ID_MAX,
    UNIT_ID_MIN,
    UnitKind,
)
from .models import ConfigurationError, GatewayConfig, UnitDefinition

#: Substrings of :class:`~.models.ConfigurationError` messages mapped to the
#: error keys under ``config_subentries.unit.error``. Checked in order, so the
#: more specific entries come first.
_ERROR_KEYS: Final[tuple[tuple[str, str], ...]] = (
    ("more dimmer names", "too_many_names"),
    ("dimmer_count", "dimmer_count_invalid"),
    ("kelvin", "kelvin_range"),
    ("name must not be empty", "name_required"),
    ("target_id", "target_id_invalid"),
)

_ERROR_FALLBACK: Final = "unknown"

#: Address range per kind, used for the number field and for the hint text.
_TARGET_RANGES: Final[dict[UnitKind, tuple[int, int]]] = {
    UnitKind.GROUP: (GROUP_ID_MIN, GROUP_ID_MAX),
    UnitKind.SCENE: (SCENE_ID_MIN, SCENE_ID_MAX),
}

_DEFAULT_TARGET_RANGE: Final = (UNIT_ID_MIN, UNIT_ID_MAX)


def _error_key(err: ConfigurationError) -> str:
    """Map a model validation error onto a translated error key."""
    message = str(err)
    for needle, key in _ERROR_KEYS:
        if needle in message:
            return key
    return _ERROR_FALLBACK


def _split_names(value: Any) -> list[str]:
    """Turn the multi line driver name field into a list of names.

    One name per line. Interior blank lines are kept so a single driver can be
    left to its numbered default, trailing blank lines are dropped.
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        names = [str(item).strip() for item in value]
    else:
        names = [line.strip() for line in str(value).splitlines()]
    while names and not names[-1]:
        names.pop()
    return names


def _target_range(kind: UnitKind) -> tuple[int, int]:
    """Return the lowest and highest address this kind may use."""
    return _TARGET_RANGES.get(kind, _DEFAULT_TARGET_RANGE)


def _number(minimum: int, maximum: int) -> NumberSelector:
    """Build a plain integer input box."""
    return NumberSelector(
        NumberSelectorConfig(
            min=minimum, max=maximum, step=1, mode=NumberSelectorMode.BOX
        )
    )


class UnitSubentryFlow(ConfigSubentryFlow):
    """Add or edit one Casambi element below a gateway entry."""

    # ------------------------------------------------------------- adding --

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Let the user pick which kind of element to add."""
        return self.async_show_menu(
            step_id="user",
            menu_options=[str(kind) for kind in IMPLEMENTED_KINDS],
        )

    async def async_step_simple(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a plain dimmable luminaire."""
        return await self._async_add(UnitKind.SIMPLE, user_input)

    async def async_step_tunable_white(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a luminaire with colour temperature."""
        return await self._async_add(UnitKind.TUNABLE_WHITE, user_input)

    async def async_step_multi_dali(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a unit that drives several DALI drivers."""
        return await self._async_add(UnitKind.MULTI_DALI, user_input)

    async def async_step_group(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a Casambi group."""
        return await self._async_add(UnitKind.GROUP, user_input)

    async def async_step_switch(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a switching output such as a fan."""
        return await self._async_add(UnitKind.SWITCH, user_input)

    async def async_step_scene(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a Casambi scene."""
        return await self._async_add(UnitKind.SCENE, user_input)

    async def async_step_broadcast(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add the "all luminaires" element."""
        return await self._async_add(UnitKind.BROADCAST, user_input)

    async def _async_add(
        self, kind: UnitKind, user_input: dict[str, Any] | None
    ) -> SubentryFlowResult:
        """Show and handle the form of one kind."""
        errors: dict[str, str] = {}
        values = self._form_values(kind, None)

        if user_input is not None:
            values.update(user_input)
            try:
                definition = UnitDefinition.from_dict(self._as_stored(kind, values))
            except ConfigurationError as err:
                errors["base"] = _error_key(err)
            else:
                if self._is_duplicate(definition):
                    errors["base"] = "duplicate_target"
                else:
                    return self.async_create_entry(
                        title=definition.name, data=definition.to_dict()
                    )

        return self.async_show_form(
            step_id=str(kind),
            data_schema=self._schema(kind, values, with_target=True),
            errors=errors,
        )

    # ------------------------------------------------------------ editing --

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Edit an existing element, keeping its kind and address.

        Setup skips an element whose stored data cannot be read, but it stays
        visible on the integration page, so editing it is the one way out of
        that state. It must therefore not be the one path without a safety net.
        """
        subentry = self._get_reconfigure_subentry()
        try:
            existing = UnitDefinition.from_dict(dict(subentry.data))
        except ConfigurationError:
            return self.async_abort(reason="element_unreadable")
        kind = existing.kind

        errors: dict[str, str] = {}
        values = self._form_values(kind, existing)

        if user_input is not None:
            values.update(user_input)
            try:
                definition = UnitDefinition.from_dict(
                    self._as_stored(kind, values, existing)
                )
            except ConfigurationError as err:
                errors["base"] = _error_key(err)
            else:
                return self.async_update_and_abort(
                    self._get_entry(),
                    subentry,
                    title=definition.name,
                    data=definition.to_dict(),
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self._schema(kind, values, with_target=False),
            errors=errors,
            description_placeholders={
                "kind": str(kind),
                "address": "-" if kind in TARGETLESS_KINDS else str(existing.target_id),
            },
        )

    # -------------------------------------------------------------- forms --

    def _schema(
        self, kind: UnitKind, values: Mapping[str, Any], *, with_target: bool
    ) -> vol.Schema:
        """Build the form of one kind, prefilled with ``values``.

        ``with_target`` is false while editing, because the address is part of
        the unique id and therefore not changeable.
        """
        fields: dict[Any, Any] = {
            vol.Required(CONF_NAME, default=str(values[CONF_NAME])): TextSelector(
                TextSelectorConfig(type=TextSelectorType.TEXT)
            )
        }

        if with_target and kind not in TARGETLESS_KINDS:
            low, high = _target_range(kind)
            fields[
                vol.Required(CONF_TARGET_ID, default=int(values[CONF_TARGET_ID]))
            ] = _number(low, high)

        if kind is UnitKind.TUNABLE_WHITE:
            fields[
                vol.Required(CONF_MIN_KELVIN, default=int(values[CONF_MIN_KELVIN]))
            ] = _number(KELVIN_FORM_MIN, KELVIN_FORM_MAX)
            fields[
                vol.Required(CONF_MAX_KELVIN, default=int(values[CONF_MAX_KELVIN]))
            ] = _number(KELVIN_FORM_MIN, KELVIN_FORM_MAX)

        if kind is UnitKind.MULTI_DALI:
            fields[
                vol.Required(CONF_DIMMER_COUNT, default=int(values[CONF_DIMMER_COUNT]))
            ] = _number(1, MAX_DIMMER_COUNT)
            fields[
                vol.Optional(CONF_DIMMER_NAMES, default=str(values[CONF_DIMMER_NAMES]))
            ] = TextSelector(TextSelectorConfig(multiline=True))
            fields[
                vol.Required(
                    CONF_WITH_TOTAL_ENTITY, default=bool(values[CONF_WITH_TOTAL_ENTITY])
                )
            ] = BooleanSelector()

        if kind is UnitKind.SWITCH:
            fields[
                vol.Required(
                    CONF_SWITCH_DOMAIN, default=str(values[CONF_SWITCH_DOMAIN])
                )
            ] = SelectSelector(
                SelectSelectorConfig(
                    options=[SWITCH_DOMAIN_SWITCH, SWITCH_DOMAIN_FAN],
                    mode=SelectSelectorMode.DROPDOWN,
                    translation_key="switch_domain",
                )
            )
        else:
            fields[
                vol.Required(
                    CONF_DEFAULT_ON_LEVEL, default=int(values[CONF_DEFAULT_ON_LEVEL])
                )
            ] = _number(1, LEVEL_MAX)

        if kind in (UnitKind.SIMPLE, UnitKind.TUNABLE_WHITE):
            fields[
                vol.Required(
                    CONF_OPTIMISTIC_OVERRIDE,
                    default=bool(values[CONF_OPTIMISTIC_OVERRIDE]),
                )
            ] = BooleanSelector()

        return vol.Schema(fields)

    def _form_values(
        self, kind: UnitKind, existing: UnitDefinition | None
    ) -> dict[str, Any]:
        """Collect the prefill for every field a form of ``kind`` may show."""
        if existing is not None:
            return {
                CONF_NAME: existing.name,
                CONF_TARGET_ID: existing.target_id,
                CONF_MIN_KELVIN: existing.min_kelvin,
                CONF_MAX_KELVIN: existing.max_kelvin,
                CONF_DIMMER_COUNT: existing.dimmer_count,
                CONF_DIMMER_NAMES: "\n".join(existing.dimmer_names),
                CONF_WITH_TOTAL_ENTITY: existing.with_total_entity,
                CONF_DEFAULT_ON_LEVEL: existing.default_on_level,
                CONF_OPTIMISTIC_OVERRIDE: bool(existing.optimistic_override),
                CONF_SWITCH_DOMAIN: existing.switch_domain,
            }

        config = self._gateway_config()
        low, _high = _target_range(kind)
        return {
            CONF_NAME: "",
            CONF_TARGET_ID: 0 if kind in TARGETLESS_KINDS else low,
            CONF_MIN_KELVIN: config.default_min_kelvin,
            CONF_MAX_KELVIN: config.default_max_kelvin,
            CONF_DIMMER_COUNT: 1,
            CONF_DIMMER_NAMES: "",
            CONF_WITH_TOTAL_ENTITY: True,
            CONF_DEFAULT_ON_LEVEL: DEFAULT_ON_LEVEL,
            CONF_OPTIMISTIC_OVERRIDE: False,
            CONF_SWITCH_DOMAIN: SWITCH_DOMAIN_SWITCH,
        }

    def _as_stored(
        self,
        kind: UnitKind,
        values: Mapping[str, Any],
        existing: UnitDefinition | None = None,
    ) -> dict[str, Any]:
        """Turn form values into the dictionary the model reads.

        Fields a form does not show keep the value they already had, so editing
        a switch never resets, say, the colour temperature bounds.
        """
        if kind in TARGETLESS_KINDS:
            target_id = 0
        elif existing is not None:
            target_id = existing.target_id
        else:
            target_id = int(values[CONF_TARGET_ID])

        optimistic = values[CONF_OPTIMISTIC_OVERRIDE]
        return {
            CONF_KIND: str(kind),
            CONF_NAME: str(values[CONF_NAME]).strip(),
            CONF_TARGET_ID: target_id,
            CONF_MIN_KELVIN: int(values[CONF_MIN_KELVIN]),
            CONF_MAX_KELVIN: int(values[CONF_MAX_KELVIN]),
            CONF_DIMMER_COUNT: int(values[CONF_DIMMER_COUNT]),
            CONF_DIMMER_NAMES: _split_names(values[CONF_DIMMER_NAMES]),
            CONF_WITH_TOTAL_ENTITY: bool(values[CONF_WITH_TOTAL_ENTITY]),
            CONF_DEFAULT_ON_LEVEL: int(values[CONF_DEFAULT_ON_LEVEL]),
            CONF_OPTIMISTIC_OVERRIDE: None if optimistic is None else bool(optimistic),
            CONF_SWITCH_DOMAIN: str(values[CONF_SWITCH_DOMAIN]),
        }

    # --------------------------------------------------------- entry data --

    def _gateway_config(self) -> GatewayConfig:
        """Read the bridge settings so new forms can prefill from them."""
        entry = self._get_entry()
        return GatewayConfig.from_dict({**entry.data, **entry.options})

    def _existing_definitions(
        self, entry: ConfigEntry, skip: ConfigSubentry | None = None
    ) -> Iterator[UnitDefinition]:
        """Yield the elements already configured below ``entry``."""
        for subentry in entry.subentries.values():
            if subentry.subentry_type != SUBENTRY_TYPE_UNIT:
                continue
            if skip is not None and subentry.subentry_id == skip.subentry_id:
                continue
            try:
                yield UnitDefinition.from_dict(dict(subentry.data))
            except ConfigurationError:
                continue

    def _is_duplicate(self, definition: UnitDefinition) -> bool:
        """Whether an element with the same identity already exists.

        Identity is the unique id, not the pair of kind and address, because
        several kinds share one address space: a dimmable luminaire, a tunable
        white one, a unit with several drivers and a switching output all
        address a Casambi *unit*, so they all produce ``..._u<id>``. Two of
        them on the same address would collide, and Home Assistant would drop
        the entities of whichever loaded second, without telling anyone.

        A unit and a group may still carry the same number, because their
        unique ids differ.
        """
        bridge_id = self._gateway_config().bridge_id
        unique_id = definition.base_unique_id(bridge_id)
        return any(
            other.base_unique_id(bridge_id) == unique_id
            for other in self._existing_definitions(self._get_entry())
        )
