"""Light platform for Govee LAN Control."""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_EFFECT,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util.color import brightness_to_value, value_to_brightness

from .const import DOMAIN, MANUFACTURER
from .coordinator import GoveeConfigEntry, GoveeCoordinator, GoveeLight

# Commands go out immediately; the coordinator serializes per light.
PARALLEL_UPDATES = 0

BRIGHTNESS_SCALE = (1, 100)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GoveeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a light entity for every light the coordinator knows about."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_new() -> None:
        new = [
            GoveeLightEntity(coordinator, light)
            for device_id, light in coordinator.lights.items()
            if device_id not in known
        ]
        if new:
            known.update(entity.light.device_id for entity in new)
            async_add_entities(new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


def unique_id_for(light: GoveeLight) -> str:
    """Return the unique ID. Existing entities are matched on this exact format."""
    return f"{light.sku}_{light.device_id.replace(':', '')}"


class GoveeLightEntity(CoordinatorEntity[GoveeCoordinator], LightEntity):
    """A Govee light."""

    _attr_has_entity_name = True
    _attr_name = None

    def __init__(self, coordinator: GoveeCoordinator, light: GoveeLight) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self.light = light
        self._attr_unique_id = unique_id_for(light)

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device, keeping the identifier earlier versions registered."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.light.device_id)},
            name=self.light.name,
            manufacturer=MANUFACTURER,
            model=self.light.sku,
        )

    @property
    def available(self) -> bool:
        """Return whether the light can be reached."""
        return self.light.available

    @property
    def supported_color_modes(self) -> set[ColorMode]:
        """Return the color modes this light has."""
        modes: set[ColorMode] = set()
        if self.light.supports_rgb:
            modes.add(ColorMode.RGB)
        if self.light.supports_kelvin:
            modes.add(ColorMode.COLOR_TEMP)
        if modes:
            return modes
        return {
            ColorMode.BRIGHTNESS if self.light.supports_brightness else ColorMode.ONOFF
        }

    @property
    def color_mode(self) -> ColorMode:
        """Return the mode the light is in."""
        modes = self.supported_color_modes
        state = self.light.state
        if ColorMode.COLOR_TEMP in modes and state and state.kelvin > 0:
            return ColorMode.COLOR_TEMP
        if ColorMode.RGB in modes:
            return ColorMode.RGB
        return next(iter(modes))

    @property
    def is_on(self) -> bool | None:
        """Return whether the light is on."""
        return self.light.state.on if self.light.state else None

    @property
    def brightness(self) -> int | None:
        """Return brightness on HA's 1-255 scale."""
        state = self.light.state
        if not state or not self.light.supports_brightness:
            return None
        return value_to_brightness(BRIGHTNESS_SCALE, max(state.brightness_pct, 1))

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        """Return the color."""
        state = self.light.state
        if not state:
            return None
        # HA requires a color in RGB mode, and a write can leave none recorded.
        return state.rgb or (255, 255, 255)

    @property
    def color_temp_kelvin(self) -> int | None:
        """Return the color temperature, when in that mode."""
        state = self.light.state
        return state.kelvin if state and state.kelvin > 0 else None

    @property
    def min_color_temp_kelvin(self) -> int:
        """Return the warmest color temperature."""
        return self.light.kelvin_range[0]

    @property
    def max_color_temp_kelvin(self) -> int:
        """Return the coolest color temperature."""
        return self.light.kelvin_range[1]

    @property
    def supported_features(self) -> LightEntityFeature:
        """Return effects support, once the list has loaded."""
        return (
            LightEntityFeature.EFFECT if self.light.effects else LightEntityFeature(0)
        )

    @property
    def effect_list(self) -> list[str] | None:
        """Return the available effects."""
        return list(self.light.effects) or None

    @property
    def effect(self) -> str | None:
        """Return the effect last set from Home Assistant."""
        return self.light.effect

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return how the light is being reached, for troubleshooting."""
        return {
            "lan_enabled": self.light.lan_reachable,
            "http_enabled": self.light.cloud is not None,
            "cloud_online": self.light.cloud_online,
            "ip_address": self.light.lan.ip if self.light.lan else None,
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on, optionally changing brightness, color or effect."""
        brightness_pct = None
        if ATTR_BRIGHTNESS in kwargs:
            value = brightness_to_value(BRIGHTNESS_SCALE, kwargs[ATTR_BRIGHTNESS])
            brightness_pct = max(1, min(100, round(value)))
        kelvin = None
        if ATTR_COLOR_TEMP_KELVIN in kwargs:
            low, high = self.light.kelvin_range
            kelvin = max(low, min(high, int(kwargs[ATTR_COLOR_TEMP_KELVIN])))
        await self.coordinator.async_turn_on(
            self.light,
            brightness_pct=brightness_pct,
            rgb=kwargs.get(ATTR_RGB_COLOR),
            kelvin=kelvin,
            effect=kwargs.get(ATTR_EFFECT),
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off."""
        await self.coordinator.async_turn_off(self.light)
