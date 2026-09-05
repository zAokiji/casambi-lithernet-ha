"""Light entities for Casambi scenes and for the whole network.

Hooked into the light platform at the one
marked place in :func:`~.light.async_setup_entry`. Project document 15.1 and
15.2:

``scene``
    A scene defined in the Casambi app, recalled with ``set/scene_level``.
    Recalling it with a brightness is one command; level 0 switches it off.
    The gateway polls ``poll_scene/<n>`` in every polling mode, so the state is
    real and the entity is not optimistic.
``broadcast``
    Every luminaire in the network at once on ``set/level`` -- one radio
    command instead of twelve, which is what makes it the "all off" entity and
    the way back out of "everything sits at 1 %".

Both kinds are addressed without a unit id, so neither has a ``propertys``
topic; availability therefore hangs on the broker connection alone, which the
base class already handles through ``UnitDefinition.has_properties``.
"""

from __future__ import annotations

from homeassistant.core import callback

from .const import UnitKind
from .contracts import CasambiGateway
from .light import CasambiLight
from .models import UnitDefinition
from .state import SceneValues


def build_scene_broadcast_lights(
    gateway: CasambiGateway, definition: UnitDefinition
) -> list[CasambiLight]:
    """Build the light entities of a scene or broadcast element.

    Returns an empty list for every other kind, so the light platform can call
    this next to :func:`~.light.build_lights` without either handling an
    element twice.
    """
    if definition.kind is UnitKind.SCENE:
        return [CasambiSceneLight(gateway, definition)]
    if definition.kind is UnitKind.BROADCAST:
        return [CasambiBroadcastLight(gateway, definition)]
    return []


class CasambiSceneLight(CasambiLight):
    """A Casambi scene as a dimmable light.

    Switching on recalls the scene with a brightness, switching off recalls it
    with level 0. The state comes from ``poll_scene/<n>``, which reports
    ``active`` and ``level`` separately: a scene that is not active still
    reports the level it would be recalled with (the reference installation
    sends ``{"active":0,"level":255}``), so both fields have to agree before
    the entity counts as on. :attr:`~.state.SceneValues.is_on` does exactly
    that.
    """

    def __init__(self, gateway: CasambiGateway, definition: UnitDefinition) -> None:
        """Name the entity after its device and use the scene's unique id."""
        super().__init__(
            gateway,
            definition,
            unique_id=definition.base_unique_id(gateway.config.bridge_id),
        )

    async def _async_send_level(self, level: int, duration_ms: int | None) -> None:
        """Recall the scene at ``level``; 0 switches it off."""
        await self._gateway.async_set_scene_level(
            self._definition.target_id, level, duration_ms
        )

    @callback
    def _read_initial_state(self) -> None:
        """Take the last scene state the gateway holds, if any."""
        values = self._gateway.scene_values(self._definition.target_id)
        if values is not None:
            self._apply_scene(values)

    @callback
    def _register_subscriptions(self) -> None:
        """Follow ``poll_scene/<id>``."""
        self.async_on_remove(
            self._gateway.subscribe_scene(
                self._definition.target_id, self._handle_scene
            )
        )

    @callback
    def _apply_scene(self, values: SceneValues) -> None:
        """Adopt a scene state message.

        The brightness is only shown while the scene is on; an inactive scene
        must not present its stored recall level as if it were lit.
        """
        self._attr_is_on = values.is_on
        self._attr_brightness = values.level if values.is_on else None

    @callback
    def _handle_scene(self, values: SceneValues) -> None:
        """Adopt an incoming scene state, unless a command is still pending.

        A scene reports its recall level rather than the level it was called
        with, so there is nothing to compare; any message counts as the answer.
        """
        self._state_confirmed()
        self._apply_scene(values)
        self.async_write_ha_state()


class CasambiBroadcastLight(CasambiLight):
    """Every luminaire of the network as one light.

    Caveat on the state, and the reason this entity carries
    ``assumed_state``: ``poll_broadcast`` reports an **average** level across
    the whole network, not the brightness of any one luminaire. Twelve
    luminaires at 1 % and one at full look the same as a network at 20 %. The
    average is still worth showing, because it is precisely the number that
    reveals "everything sits at 1 %", but Home Assistant is told not to treat
    it as a confirmed state: the entity gets separate on and off buttons
    instead of a toggle, and a command is adopted right away rather than
    waited on.
    """

    def __init__(self, gateway: CasambiGateway, definition: UnitDefinition) -> None:
        """Use the network wide unique id and never wait for confirmation."""
        super().__init__(
            gateway,
            definition,
            unique_id=definition.base_unique_id(gateway.config.bridge_id),
            force_optimistic=True,
        )

    async def _async_send_level(self, level: int, duration_ms: int | None) -> None:
        """Set the whole network with a single radio command."""
        await self._gateway.async_set_broadcast_level(level, duration_ms)

    @callback
    def _read_initial_state(self) -> None:
        """Take the last broadcast values the gateway holds, if any."""
        values = self._gateway.broadcast_values()
        if values is not None:
            self._apply_level(values.level)

    @callback
    def _register_subscriptions(self) -> None:
        """Follow ``poll_broadcast``."""
        self.async_on_remove(self._gateway.subscribe_broadcast(self._handle_values))
