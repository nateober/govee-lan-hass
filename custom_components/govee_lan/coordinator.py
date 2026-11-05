"""Merges LAN and cloud into one view of each light and routes commands."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field, replace

from govee_local_api import GoveeController, GoveeDevice
from govee_local_api.controller import COMMAND_PORT
from govee_local_api.light_capabilities import GoveeLightFeatures
from govee_local_api.message import ColorMessage
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .cloud import (
    CAP_COLOR,
    CAP_ON_OFF,
    CAP_RANGE,
    CAP_SCENE,
    INSTANCE_BRIGHTNESS,
    INSTANCE_KELVIN,
    INSTANCE_POWER,
    INSTANCE_RGB,
    CloudDevice,
    CloudEffect,
    GoveeAuthError,
    GoveeCloud,
    GoveeCloudError,
    GoveeRateLimited,
)
from .const import (
    CLOUD_LIST_INTERVAL,
    CLOUD_MAX_FAILURES,
    CLOUD_RATE_LIMIT_BACKOFF,
    CLOUD_SCENE_INTERVAL,
    CLOUD_STATE_INTERVAL,
    CLOUD_WRITE_HOLD,
    DEFAULT_MAX_KELVIN,
    DEFAULT_MIN_KELVIN,
    DOMAIN,
    LAN_POLL_INTERVAL,
    LAN_TIMEOUT,
    LAN_WRITE_HOLD,
)

_LOGGER = logging.getLogger(__name__)

type GoveeConfigEntry = ConfigEntry[GoveeCoordinator]

# Sent by HA scene snapshots and the UI to mean "no effect".
NO_EFFECT_NAMES = frozenset({"none", "off"})


@dataclass(slots=True)
class LightState:
    """What a light is doing."""

    on: bool = False
    brightness_pct: int = 100
    rgb: tuple[int, int, int] | None = None
    kelvin: int = 0


@dataclass(slots=True)
class GoveeLight:
    """One physical light, however it can currently be reached."""

    device_id: str
    sku: str
    name: str
    cloud: CloudDevice | None = None
    lan: GoveeDevice | None = None
    state: LightState | None = None
    cloud_online: bool | None = None
    cloud_failures: int = 0
    hold_until: float = 0.0
    next_state_poll: float = 0.0
    effects: dict[str, CloudEffect] = field(default_factory=dict)
    effect: str | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def lan_reachable(self) -> bool:
        """Return whether the light has answered on the LAN recently."""
        return (
            self.lan is not None and dt_util.utcnow() - self.lan.lastseen < LAN_TIMEOUT
        )

    @property
    def available(self) -> bool:
        """Return whether a command could reach the light."""
        return self.state is not None and (
            self.lan_reachable or self.cloud_online is True
        )

    @property
    def kelvin_range(self) -> tuple[int, int]:
        """Return the color temperature range, preferring what Govee declares."""
        if self.cloud and (declared := self.cloud.kelvin_range):
            return declared
        if self.lan:
            value_range = self.lan.temperature_range
            return (value_range.min_kelvin, value_range.max_kelvin)
        return (DEFAULT_MIN_KELVIN, DEFAULT_MAX_KELVIN)

    @property
    def supports_rgb(self) -> bool:
        """Return whether the light can show a color."""
        if self.cloud:
            return self.cloud.has(INSTANCE_RGB)
        return self._lan_has(GoveeLightFeatures.COLOR_RGB)

    @property
    def supports_kelvin(self) -> bool:
        """Return whether the light has a color temperature mode."""
        if self.cloud:
            return self.cloud.has(INSTANCE_KELVIN)
        return self._lan_has(GoveeLightFeatures.COLOR_KELVIN_TEMPERATURE)

    @property
    def supports_brightness(self) -> bool:
        """Return whether the light can be dimmed."""
        if self.cloud:
            return self.cloud.has(INSTANCE_BRIGHTNESS)
        return self._lan_has(GoveeLightFeatures.BRIGHTNESS)

    def _lan_has(self, feature: GoveeLightFeatures) -> bool:
        capabilities = self.lan.capabilities if self.lan else None
        return bool(capabilities and capabilities.features & feature)


def _fallback_name(device_id: str, sku: str) -> str:
    return f"{sku}_{device_id.replace(':', '')[-4:].upper()}"


class GoveeCoordinator(DataUpdateCoordinator[dict[str, GoveeLight]]):
    """Polls the LAN every tick and the cloud only for what the LAN cannot reach."""

    config_entry: GoveeConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: GoveeConfigEntry,
        listening_addresses: list[str],
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=LAN_POLL_INTERVAL,
        )
        self.lights: dict[str, GoveeLight] = {}
        api_key = entry.options.get(CONF_API_KEY) or entry.data.get(CONF_API_KEY)
        self.cloud = (
            GoveeCloud(async_get_clientsession(hass), api_key) if api_key else None
        )
        self.controller = GoveeController(
            loop=hass.loop,
            logger=_LOGGER,
            listening_addresses=listening_addresses,
            discovery_enabled=True,
            update_enabled=False,
            discovered_callback=self._on_lan_discovered,
        )
        self._cloud_task: asyncio.Task | None = None
        self._cloud_paused_until = 0.0
        self._next_list = 0.0
        self._next_scenes = 0.0
        self._first_refresh_done = False

    async def async_start(self) -> None:
        """Open the LAN sockets. Raises OSError when none can be bound."""
        await self.controller.start(require_all=False)
        self.controller.send_update_message()

    def async_stop(self) -> asyncio.Event:
        """Close the LAN sockets and stop background cloud work."""
        if self._cloud_task:
            self._cloud_task.cancel()
        return self.controller.cleanup()

    # LAN side

    @callback
    def _on_lan_discovered(self, device: GoveeDevice, _is_new: bool) -> bool:
        light = self.lights.get(device.fingerprint)
        if light is None:
            light = GoveeLight(
                device_id=device.fingerprint,
                sku=device.sku,
                name=_fallback_name(device.fingerprint, device.sku),
            )
            self.lights[device.fingerprint] = light
        light.lan = device
        device.set_update_callback(self._on_lan_update)
        # The library registers the device after this callback returns, so a
        # status request sent now would not include it.
        self.hass.loop.call_soon(self.controller.send_update_message)
        self.async_update_listeners()
        return True

    @callback
    def _on_lan_update(self, device: GoveeDevice) -> None:
        light = self.lights.get(device.fingerprint)
        if light is None or time.monotonic() < light.hold_until:
            return
        light.state = LightState(
            on=device.on,
            brightness_pct=device.brightness,
            rgb=device.rgb_color,
            kelvin=device.temperature_color or 0,
        )
        self.async_update_listeners()

    # Polling

    async def _async_update_data(self) -> dict[str, GoveeLight]:
        if not self.controller.protocols:
            self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)
            raise UpdateFailed(
                translation_domain=DOMAIN, translation_key="no_live_endpoints"
            )
        self.controller.send_update_message()

        if self.cloud:
            if not self._first_refresh_done:
                # Awaited so entities exist with real state as soon as setup ends.
                self._first_refresh_done = True
                try:
                    await self._async_cloud_pass()
                except GoveeAuthError as err:
                    raise ConfigEntryAuthFailed(str(err)) from err
            elif self._cloud_task is None or self._cloud_task.done():
                # Never awaited after that: a slow cloud must not delay the LAN.
                self._cloud_task = self.config_entry.async_create_background_task(
                    self.hass, self._async_background_cloud_pass(), "govee_lan cloud"
                )
        return self.lights

    async def _async_background_cloud_pass(self) -> None:
        try:
            await self._async_cloud_pass()
        except GoveeAuthError:
            _LOGGER.warning("Govee rejected the API key; asking for a new one")
            self.cloud = None
            self.config_entry.async_start_reauth(self.hass)
        except Exception:
            # Govee changing a response shape must be visible, and must not stop
            # the next pass from running.
            _LOGGER.exception("Unexpected error polling the Govee cloud")
        self.async_update_listeners()

    async def _async_cloud_pass(self) -> None:
        now = time.monotonic()
        if now < self._cloud_paused_until:
            return
        try:
            if now >= self._next_list:
                await self._async_refresh_list()
                self._next_list = now + CLOUD_LIST_INTERVAL
            await self._async_refresh_states(now)
            if now >= self._next_scenes:
                await self._async_refresh_effects()
                self._next_scenes = now + CLOUD_SCENE_INTERVAL
        except GoveeRateLimited:
            _LOGGER.warning(
                "Govee API limit reached; pausing cloud polling for %d minutes",
                CLOUD_RATE_LIMIT_BACKOFF // 60,
            )
            self._cloud_paused_until = now + CLOUD_RATE_LIMIT_BACKOFF
        except GoveeAuthError:
            raise
        except GoveeCloudError as err:
            # The next pass retries; LAN lights are unaffected.
            _LOGGER.debug("Govee cloud pass failed: %s", err)

    async def _async_refresh_list(self) -> None:
        assert self.cloud
        for device in await self.cloud.async_get_lights():
            light = self.lights.get(device.device_id)
            if light is None:
                light = GoveeLight(
                    device_id=device.device_id, sku=device.sku, name=device.name
                )
                self.lights[device.device_id] = light
            light.cloud = device
            light.name = device.name

    async def _async_refresh_states(self, now: float) -> None:
        assert self.cloud
        for light in list(self.lights.values()):
            if (
                light.cloud is None
                or light.lan_reachable
                or now < light.next_state_poll
            ):
                continue
            light.next_state_poll = now + CLOUD_STATE_INTERVAL
            try:
                state = await self.cloud.async_get_state(light.cloud)
            except (GoveeAuthError, GoveeRateLimited):
                raise
            except GoveeCloudError as err:
                light.cloud_failures += 1
                _LOGGER.debug("State read failed for %s: %s", light.name, err)
                if light.cloud_failures >= CLOUD_MAX_FAILURES:
                    light.cloud_online = None
                continue
            light.cloud_failures = 0
            # A successful read with no online field is not evidence of being offline.
            light.cloud_online = True if state.online is None else state.online
            if time.monotonic() >= light.hold_until:
                light.state = LightState(
                    on=state.on,
                    brightness_pct=state.brightness_pct,
                    rgb=state.rgb,
                    kelvin=state.kelvin,
                )

    async def _async_refresh_effects(self) -> None:
        assert self.cloud
        for light in list(self.lights.values()):
            if light.cloud is None or not light.cloud.has_scenes:
                continue
            try:
                light.effects = await self.cloud.async_get_effects(light.cloud)
            except (GoveeAuthError, GoveeRateLimited):
                raise
            except GoveeCloudError as err:
                _LOGGER.debug("Effects read failed for %s: %s", light.name, err)

    # Commands

    async def async_turn_on(
        self,
        light: GoveeLight,
        *,
        brightness_pct: int | None = None,
        rgb: tuple[int, int, int] | None = None,
        kelvin: int | None = None,
        effect: str | None = None,
    ) -> None:
        """Apply a turn_on, by LAN when the light answers there."""
        scene = self._resolve_effect(light, effect)

        async with light.lock:
            # Read under the lock: a turn_off queued ahead of us changes the answer.
            current = light.state or LightState()
            # A plain turn_on must send power; so must any change to a light that
            # is off, because a brightness or color write does not always wake it.
            needs_power = not current.on or (
                brightness_pct is None
                and rgb is None
                and kelvin is None
                and scene is None
            )
            if light.lan_reachable:
                await self._async_lan_on(
                    light, needs_power, brightness_pct, rgb, kelvin
                )
                hold = LAN_WRITE_HOLD
            else:
                await self._async_cloud_on(
                    light, needs_power, brightness_pct, rgb, kelvin
                )
                hold = CLOUD_WRITE_HOLD
            if scene:
                await self._async_cloud_call(
                    light, CAP_SCENE, scene.instance, scene.value
                )
                hold = CLOUD_WRITE_HOLD

            new = replace(current, on=True)
            if brightness_pct is not None:
                new.brightness_pct = brightness_pct
            if rgb is not None:
                new.rgb, new.kelvin = rgb, 0
                light.effect = None
            elif kelvin is not None:
                new.kelvin, new.rgb = kelvin, None
                light.effect = None
            if scene:
                light.effect = effect
            elif effect and effect.lower() in NO_EFFECT_NAMES:
                light.effect = None
            self._apply_write(light, new, hold)

    async def async_turn_off(self, light: GoveeLight) -> None:
        """Turn a light off."""
        async with light.lock:
            if light.lan_reachable:
                await light.lan.turn_off()
                hold = LAN_WRITE_HOLD
            else:
                await self._async_cloud_call(light, CAP_ON_OFF, INSTANCE_POWER, 0)
                hold = CLOUD_WRITE_HOLD
            light.effect = None
            self._apply_write(
                light, replace(light.state or LightState(), on=False), hold
            )

    def _resolve_effect(
        self, light: GoveeLight, effect: str | None
    ) -> CloudEffect | None:
        if not effect or effect.lower() in NO_EFFECT_NAMES:
            return None
        scene = light.effects.get(effect)
        if scene is None:
            # HA scenes replay old snapshots, so a stale name must not fail them.
            _LOGGER.warning("%s has no effect named %r", light.name, effect)
        return scene

    @callback
    def _apply_write(self, light: GoveeLight, state: LightState, hold: float) -> None:
        light.state = state
        light.hold_until = time.monotonic() + hold
        light.next_state_poll = max(light.next_state_poll, light.hold_until)
        self.async_update_listeners()

    async def _async_lan_on(
        self,
        light: GoveeLight,
        power: bool,
        brightness_pct: int | None,
        rgb: tuple[int, int, int] | None,
        kelvin: int | None,
    ) -> None:
        device = light.lan
        assert device
        if power:
            await device.turn_on()
        if brightness_pct is not None:
            await device.set_brightness(brightness_pct)
        if rgb is not None:
            await device.set_rgb_color(*rgb)
        elif kelvin is not None:
            await self._async_lan_set_kelvin(light, device, kelvin)

    @staticmethod
    async def _async_lan_set_kelvin(
        light: GoveeLight, device: GoveeDevice, kelvin: int
    ) -> None:
        """Send a color temperature using the range Govee declares for this light.

        device.set_temperature() clamps to the library's own per-model table,
        which can disagree with the cloud. It holds H6076 at 2700 K where the
        cloud declares 2200 K, and would visibly change warm-white automations.
        """
        transport = device.transport
        if transport is None or transport.is_closing():
            # The socket was replaced. Let the library pick a live one, accepting
            # its range, rather than dropping the command silently.
            await device.set_temperature(kelvin)
            return
        message = ColorMessage(
            rgb=None, temperature=kelvin, temperature_range=light.kelvin_range
        )
        transport.sendto(bytes(message), (device.ip, COMMAND_PORT))

    async def _async_cloud_on(
        self,
        light: GoveeLight,
        power: bool,
        brightness_pct: int | None,
        rgb: tuple[int, int, int] | None,
        kelvin: int | None,
    ) -> None:
        if power:
            await self._async_cloud_call(light, CAP_ON_OFF, INSTANCE_POWER, 1)
        if brightness_pct is not None:
            await self._async_cloud_call(
                light, CAP_RANGE, INSTANCE_BRIGHTNESS, brightness_pct
            )
        if rgb is not None:
            value = (rgb[0] << 16) | (rgb[1] << 8) | rgb[2]
            await self._async_cloud_call(light, CAP_COLOR, INSTANCE_RGB, value)
        elif kelvin is not None:
            await self._async_cloud_call(light, CAP_COLOR, INSTANCE_KELVIN, kelvin)

    async def _async_cloud_call(
        self, light: GoveeLight, cap_type: str, instance: str, value: object
    ) -> None:
        placeholders = {"name": light.name}
        if self.cloud is None or light.cloud is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="unreachable",
                translation_placeholders=placeholders,
            )
        try:
            await self.cloud.async_control(light.cloud, cap_type, instance, value)
        except GoveeRateLimited as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="rate_limited",
                translation_placeholders=placeholders,
            ) from err
        except GoveeCloudError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={**placeholders, "error": str(err)},
            ) from err
