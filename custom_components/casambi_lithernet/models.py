"""Configuration data model.

Owned by package A. Config flow (C), subentry flow (D) and every platform read
and write these objects; nobody parses raw dictionaries.

Both directions are supported: :meth:`from_dict` accepts what Home Assistant
stored in ``.storage``, :meth:`to_dict` produces what gets stored.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Self

from .const import (
    CONF_BRIDGE_ID,
    CONF_DEFAULT_DURATION_MS,
    CONF_DEFAULT_MAX_KELVIN,
    CONF_DEFAULT_MIN_KELVIN,
    CONF_DEFAULT_ON_LEVEL,
    CONF_DIMMER_COUNT,
    CONF_DIMMER_NAMES,
    CONF_GATEWAY_HOST,
    CONF_KIND,
    CONF_MAX_KELVIN,
    CONF_MIN_KELVIN,
    CONF_NAME,
    CONF_OPTIMISTIC_OVERRIDE,
    CONF_POLLING_METHOD,
    CONF_SWITCH_DOMAIN,
    CONF_TARGET_ID,
    CONF_TOPIC_PREFIX,
    CONF_WITH_TOTAL_ENTITY,
    DEFAULT_BRIDGE_ID,
    DEFAULT_DURATION_MS,
    DEFAULT_GATEWAY_HOST,
    DEFAULT_MAX_KELVIN,
    DEFAULT_MIN_KELVIN,
    DEFAULT_ON_LEVEL,
    DEFAULT_POLLING_METHOD,
    DEFAULT_TOPIC_PREFIX,
    DOMAIN,
    GROUP_ID_MAX,
    GROUP_ID_MIN,
    LEVEL_MAX,
    MAX_DIMMER_COUNT,
    POLLING_WITH_STATE,
    SCENE_ID_MAX,
    SCENE_ID_MIN,
    SWITCH_DOMAIN_FAN,
    SWITCH_DOMAIN_SWITCH,
    TARGETLESS_KINDS,
    UNIT_ADDRESSED_KINDS,
    UNIT_ID_MAX,
    UNIT_ID_MIN,
    PollingMethod,
    TargetType,
    UnitKind,
)


class ConfigurationError(ValueError):
    """Raised when a stored or submitted configuration is not usable."""


@dataclass(frozen=True, slots=True)
class GatewayConfig:
    """Everything the integration knows about one gateway bridge."""

    bridge_id: int = DEFAULT_BRIDGE_ID
    topic_prefix: str = DEFAULT_TOPIC_PREFIX
    gateway_host: str = DEFAULT_GATEWAY_HOST
    polling_method: PollingMethod = DEFAULT_POLLING_METHOD
    default_duration_ms: int = DEFAULT_DURATION_MS
    default_min_kelvin: int = DEFAULT_MIN_KELVIN
    default_max_kelvin: int = DEFAULT_MAX_KELVIN

    @property
    def delivers_state(self) -> bool:
        """Whether the configured polling method reports state at all."""
        return self.polling_method in POLLING_WITH_STATE

    @property
    def device_unique_id(self) -> str:
        """Unique id of the gateway device in the device registry."""
        return f"{DOMAIN}_{self.bridge_id}"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Build from stored config entry data merged with options."""
        try:
            polling = PollingMethod(
                data.get(CONF_POLLING_METHOD, DEFAULT_POLLING_METHOD)
            )
        except ValueError:
            polling = DEFAULT_POLLING_METHOD
        return cls(
            bridge_id=int(data.get(CONF_BRIDGE_ID, DEFAULT_BRIDGE_ID)),
            topic_prefix=str(data.get(CONF_TOPIC_PREFIX, DEFAULT_TOPIC_PREFIX)),
            gateway_host=str(data.get(CONF_GATEWAY_HOST, DEFAULT_GATEWAY_HOST)),
            polling_method=polling,
            default_duration_ms=int(
                data.get(CONF_DEFAULT_DURATION_MS, DEFAULT_DURATION_MS)
            ),
            default_min_kelvin=int(
                data.get(CONF_DEFAULT_MIN_KELVIN, DEFAULT_MIN_KELVIN)
            ),
            default_max_kelvin=int(
                data.get(CONF_DEFAULT_MAX_KELVIN, DEFAULT_MAX_KELVIN)
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise for storage in the config entry."""
        return {
            CONF_BRIDGE_ID: self.bridge_id,
            CONF_TOPIC_PREFIX: self.topic_prefix,
            CONF_GATEWAY_HOST: self.gateway_host,
            CONF_POLLING_METHOD: str(self.polling_method),
            CONF_DEFAULT_DURATION_MS: self.default_duration_ms,
            CONF_DEFAULT_MIN_KELVIN: self.default_min_kelvin,
            CONF_DEFAULT_MAX_KELVIN: self.default_max_kelvin,
        }


@dataclass(frozen=True, slots=True)
class UnitDefinition:
    """One Casambi element the user added, stored as a config subentry."""

    kind: UnitKind
    name: str
    target_id: int = 0
    min_kelvin: int = DEFAULT_MIN_KELVIN
    max_kelvin: int = DEFAULT_MAX_KELVIN
    dimmer_count: int = 1
    dimmer_names: tuple[str, ...] = ()
    with_total_entity: bool = True
    default_on_level: int = DEFAULT_ON_LEVEL
    optimistic_override: bool | None = None
    switch_domain: str = SWITCH_DOMAIN_SWITCH

    # ----------------------------------------------------------------- ids --

    def base_unique_id(self, bridge_id: int) -> str:
        """Build the unique id of this element, independent of its entities.

        The infix encodes the address space so a unit and a group with the same
        number never collide: ``u`` unit, ``g`` group, ``s`` scene,
        ``broadcast`` for the whole network.
        """
        prefix = f"{DOMAIN}_{bridge_id}"
        if self.kind is UnitKind.BROADCAST:
            return f"{prefix}_broadcast"
        if self.kind is UnitKind.GROUP:
            return f"{prefix}_g{self.target_id}"
        if self.kind is UnitKind.SCENE:
            return f"{prefix}_s{self.target_id}"
        return f"{prefix}_u{self.target_id}"

    def dimmer_unique_id(self, bridge_id: int, index: int) -> str:
        """Build the unique id of one DALI dimmer of a multi driver unit."""
        return f"{self.base_unique_id(bridge_id)}_d{index}"

    def diagnostic_unique_id(self, bridge_id: int, key: str) -> str:
        """Build the unique id of a diagnostic entity from ``propertys``."""
        return f"{self.base_unique_id(bridge_id)}_{key}"

    def dimmer_name(self, index: int) -> str:
        """Display name of one dimmer, falling back to a numbered default."""
        if index < len(self.dimmer_names) and self.dimmer_names[index].strip():
            return self.dimmer_names[index].strip()
        return f"{self.name} Dimmer {index + 1}"

    # ------------------------------------------------------------- casambi --

    @property
    def target_type(self) -> TargetType:
        """How this element is addressed in a command payload."""
        if self.kind is UnitKind.BROADCAST:
            return TargetType.BROADCAST
        if self.kind is UnitKind.GROUP:
            return TargetType.GROUP
        if self.kind is UnitKind.SCENE:
            return TargetType.SCENE_ALL
        return TargetType.UNIT

    @property
    def has_properties(self) -> bool:
        """Whether the gateway publishes ``propertys`` for this element."""
        return self.kind in UNIT_ADDRESSED_KINDS

    @property
    def is_multi_dimmer(self) -> bool:
        """Whether this element produces one entity per DALI dimmer."""
        return self.kind is UnitKind.MULTI_DALI

    # ------------------------------------------------------------ storage --

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Build from stored subentry data, raising on unusable input."""
        raw_kind = data.get(CONF_KIND)
        try:
            kind = UnitKind(raw_kind)
        except ValueError as err:
            raise ConfigurationError(f"unknown kind {raw_kind!r}") from err

        name = str(data.get(CONF_NAME, "")).strip()
        if not name:
            raise ConfigurationError("name must not be empty")

        names = data.get(CONF_DIMMER_NAMES) or ()
        definition = cls(
            kind=kind,
            name=name,
            target_id=int(data.get(CONF_TARGET_ID, 0)),
            min_kelvin=int(data.get(CONF_MIN_KELVIN, DEFAULT_MIN_KELVIN)),
            max_kelvin=int(data.get(CONF_MAX_KELVIN, DEFAULT_MAX_KELVIN)),
            dimmer_count=int(data.get(CONF_DIMMER_COUNT, 1)),
            dimmer_names=tuple(str(n) for n in names),
            with_total_entity=bool(data.get(CONF_WITH_TOTAL_ENTITY, True)),
            default_on_level=int(data.get(CONF_DEFAULT_ON_LEVEL, DEFAULT_ON_LEVEL)),
            optimistic_override=data.get(CONF_OPTIMISTIC_OVERRIDE),
            switch_domain=str(data.get(CONF_SWITCH_DOMAIN, SWITCH_DOMAIN_SWITCH)),
        )
        definition.validate()
        return definition

    def to_dict(self) -> dict[str, Any]:
        """Serialise for storage in the subentry."""
        return {
            CONF_KIND: str(self.kind),
            CONF_NAME: self.name,
            CONF_TARGET_ID: self.target_id,
            CONF_MIN_KELVIN: self.min_kelvin,
            CONF_MAX_KELVIN: self.max_kelvin,
            CONF_DIMMER_COUNT: self.dimmer_count,
            CONF_DIMMER_NAMES: list(self.dimmer_names),
            CONF_WITH_TOTAL_ENTITY: self.with_total_entity,
            CONF_DEFAULT_ON_LEVEL: self.default_on_level,
            CONF_OPTIMISTIC_OVERRIDE: self.optimistic_override,
            CONF_SWITCH_DOMAIN: self.switch_domain,
        }

    # ---------------------------------------------------------- validation --

    def validate(self) -> None:
        """Check the fields that matter for this kind.

        Raises :class:`ConfigurationError` with a message key that the flows
        can show; the message is deliberately short and machine readable.
        """
        if self.kind in TARGETLESS_KINDS:
            pass
        elif self.kind is UnitKind.GROUP:
            _require_range(self.target_id, GROUP_ID_MIN, GROUP_ID_MAX, "target_id")
        elif self.kind is UnitKind.SCENE:
            _require_range(self.target_id, SCENE_ID_MIN, SCENE_ID_MAX, "target_id")
        else:
            _require_range(self.target_id, UNIT_ID_MIN, UNIT_ID_MAX, "target_id")

        if not 1 <= self.default_on_level <= LEVEL_MAX:
            raise ConfigurationError("default_on_level out of range")

        if self.kind is UnitKind.TUNABLE_WHITE:
            if self.min_kelvin >= self.max_kelvin:
                raise ConfigurationError("min_kelvin must be below max_kelvin")
            if self.min_kelvin <= 0:
                raise ConfigurationError("min_kelvin out of range")

        if self.kind is UnitKind.MULTI_DALI:
            _require_range(self.dimmer_count, 1, MAX_DIMMER_COUNT, "dimmer_count")
            if len(self.dimmer_names) > self.dimmer_count:
                raise ConfigurationError("more dimmer names than dimmers")

        if self.kind is UnitKind.SWITCH and self.switch_domain not in (
            SWITCH_DOMAIN_SWITCH,
            SWITCH_DOMAIN_FAN,
        ):
            raise ConfigurationError("switch_domain must be switch or fan")


def _require_range(value: int, low: int, high: int, field_name: str) -> None:
    if not low <= value <= high:
        raise ConfigurationError(f"{field_name} out of range")


@dataclass(slots=True)
class RuntimeData:
    """What :func:`async_setup_entry` puts on the config entry.

    Platforms read the gateway from here. Package B fills ``gateway`` with its
    implementation of :class:`~.contracts.CasambiGateway`.
    """

    config: GatewayConfig
    gateway: Any
    units: dict[str, UnitDefinition] = field(default_factory=dict)
