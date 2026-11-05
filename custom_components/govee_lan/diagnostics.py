"""Diagnostics for Govee LAN Control. The API key and device IDs are redacted."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from .coordinator import GoveeConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: GoveeConfigEntry
) -> dict[str, Any]:
    """Return how each light is being reached."""
    return {
        "cloud_configured": entry.runtime_data.cloud is not None,
        "lights": [
            {
                "sku": light.sku,
                "lan_reachable": light.lan_reachable,
                "cloud_listed": light.cloud is not None,
                "cloud_online": light.cloud_online,
                "cloud_failures": light.cloud_failures,
                "available": light.available,
                "effects": len(light.effects),
                "kelvin_range": light.kelvin_range,
            }
            for light in entry.runtime_data.lights.values()
        ],
    }
