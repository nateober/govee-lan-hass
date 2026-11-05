"""Govee LAN Control: local-first control of Govee lights."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from errno import EADDRINUSE

from govee_local_api.controller import LISTENING_PORT
from homeassistant.components import network
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import DOMAIN
from .coordinator import GoveeConfigEntry, GoveeCoordinator

PLATFORMS: list[Platform] = [Platform.LIGHT]


async def async_setup_entry(hass: HomeAssistant, entry: GoveeConfigEntry) -> bool:
    """Set up from a config entry."""
    addresses = await _async_listening_addresses(hass)
    if not addresses:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN, translation_key="no_listening_addresses"
        )

    coordinator = GoveeCoordinator(hass, entry, addresses)

    try:
        await coordinator.async_start()
    except OSError as err:
        # Everything here can clear up by itself (a late adapter, or the other
        # integration holding the port being removed), so keep retrying.
        if err.errno == EADDRINUSE:
            raise ConfigEntryNotReady(
                translation_domain=DOMAIN,
                translation_key="port_in_use",
                translation_placeholders={"port": str(LISTENING_PORT)},
            ) from err
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="bind_failed",
            translation_placeholders={"error": err.strerror or str(err)},
        ) from err

    async def _async_stop() -> None:
        with suppress(TimeoutError):
            await asyncio.wait_for(coordinator.async_stop().wait(), 1)

    entry.async_on_unload(_async_stop)

    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: GoveeConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_listening_addresses(hass: HomeAssistant) -> list[str]:
    """Return each enabled IPv4 address with its mask."""
    adapters = await network.async_get_adapters(hass)
    return sorted(
        {
            f"{ipv4['address']}/{ipv4['network_prefix']}"
            for adapter in adapters
            if adapter["enabled"]
            for ipv4 in adapter["ipv4"]
        }
    )
