"""Govee LAN Control - Scene Select Entity"""
from __future__ import annotations

import asyncio
import aiohttp
import logging
from typing import Any, Dict, List

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# API endpoints
GOVEE_API_BASE = "https://openapi.api.govee.com/router/api/v1"
SCENES_ENDPOINT = f"{GOVEE_API_BASE}/device/scenes"
CONTROL_ENDPOINT = f"{GOVEE_API_BASE}/device/control"


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, add_entities: AddEntitiesCallback
) -> None:
    """Set up Govee scene select entities."""
    api_key = entry.options.get(CONF_API_KEY, entry.data.get(CONF_API_KEY, None))

    if not api_key:
        _LOGGER.debug("No API key configured, scene select not available")
        return

    # Get registry from domain data
    registry = hass.data[DOMAIN].get("registry")
    if not registry:
        _LOGGER.warning("Device registry not available for scene setup")
        return

    # Wait a bit for devices to be discovered
    await asyncio.sleep(5)

    entities = []
    for device_id, light_entity in registry.devices.items():
        device = light_entity._govee_device
        # Try to fetch scenes for all devices (LAN or HTTP discovered)
        # The API uses model and device_id which are available for both
        scenes = await fetch_device_scenes(api_key, device.model, device.device_id)
        if scenes and len(scenes) > 1:  # More than just "None" option
            entities.append(GoveeSceneSelect(
                api_key=api_key,
                device=device,
                light_entity=light_entity,
                scenes=scenes,
            ))

    if entities:
        _LOGGER.info("Adding %d scene select entities", len(entities))
        add_entities(entities)


async def fetch_device_scenes(api_key: str, sku: str, device_id: str) -> Dict[str, Dict]:
    """Fetch available scenes for a device from Govee API."""
    headers = {
        "Govee-API-Key": api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "requestId": f"scenes_{device_id}",
        "payload": {
            "sku": sku,
            "device": device_id,
        }
    }

    scenes = {"None": None}  # Default option to clear scene

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(SCENES_ENDPOINT, headers=headers, json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    capabilities = data.get("payload", {}).get("capabilities", [])

                    for cap in capabilities:
                        if cap.get("type") == "devices.capabilities.dynamic_scene":
                            instance = cap.get("instance", "")
                            options = cap.get("parameters", {}).get("options", [])

                            for opt in options:
                                name = opt.get("name", "")
                                value = opt.get("value", {})
                                if name and value:
                                    scenes[name] = {
                                        "instance": instance,
                                        "value": value,
                                    }
                else:
                    _LOGGER.warning("Failed to fetch scenes: %s", resp.status)
    except Exception as exc:
        _LOGGER.error("Error fetching scenes: %s", exc)

    return scenes


async def set_device_scene(api_key: str, sku: str, device_id: str, instance: str, value: Dict) -> bool:
    """Set a scene on a device via Govee API."""
    headers = {
        "Govee-API-Key": api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "requestId": f"control_{device_id}",
        "payload": {
            "sku": sku,
            "device": device_id,
            "capability": {
                "type": "devices.capabilities.dynamic_scene",
                "instance": instance,
                "value": value,
            }
        }
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(CONTROL_ENDPOINT, headers=headers, json=payload) as resp:
                if resp.status == 200:
                    _LOGGER.debug("Scene set successfully")
                    return True
                else:
                    text = await resp.text()
                    _LOGGER.warning("Failed to set scene: %s - %s", resp.status, text)
                    return False
    except Exception as exc:
        _LOGGER.error("Error setting scene: %s", exc)
        return False


class GoveeSceneSelect(SelectEntity):
    """Select entity for Govee scene control."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:palette"

    def __init__(
        self,
        api_key: str,
        device,
        light_entity,
        scenes: Dict[str, Dict],
    ) -> None:
        """Initialize the scene select entity."""
        self._api_key = api_key
        self._device = device
        self._light_entity = light_entity
        self._scenes = scenes
        self._attr_current_option = "None"

        ident = device.device_id.replace(":", "")
        self._attr_unique_id = f"{device.model}_{ident}_scene"
        self._attr_name = "Scene"

    @property
    def options(self) -> List[str]:
        """Return list of available scenes."""
        return list(self._scenes.keys())

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info to link to the light entity."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._device.device_id)},
        )

    async def async_select_option(self, option: str) -> None:
        """Change the selected scene."""
        if option == "None":
            self._attr_current_option = "None"
            self.async_write_ha_state()
            return

        scene_data = self._scenes.get(option)
        if not scene_data:
            _LOGGER.warning("Unknown scene: %s", option)
            return

        success = await set_device_scene(
            self._api_key,
            self._device.model,
            self._device.device_id,
            scene_data["instance"],
            scene_data["value"],
        )

        if success:
            self._attr_current_option = option
            self.async_write_ha_state()
