"""Config flow for Govee LAN Control."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .cloud import GoveeAuthError, GoveeCloud, GoveeCloudError
from .const import DOMAIN

TITLE = "Govee LAN Control"


async def _async_validate(hass: HomeAssistant, api_key: str) -> dict[str, str]:
    """Try the key against Govee. Returns form errors."""
    try:
        await GoveeCloud(async_get_clientsession(hass), api_key).async_get_lights()
    except GoveeAuthError:
        return {"base": "invalid_auth"}
    except GoveeCloudError:
        return {"base": "cannot_connect"}
    return {}


class GoveeLanConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle setup and re-authentication."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for an optional API key."""
        errors: dict[str, str] = {}
        if user_input is not None:
            api_key = user_input.get(CONF_API_KEY)
            if api_key:
                errors = await _async_validate(self.hass, api_key)
            if not errors:
                return self.async_create_entry(
                    title=TITLE,
                    data={},
                    options={CONF_API_KEY: api_key} if api_key else {},
                )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Optional(CONF_API_KEY): str}),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start re-authentication after Govee rejected the key."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a replacement key."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await _async_validate(self.hass, user_input[CONF_API_KEY])
            if not errors:
                entry = self._get_reauth_entry()
                return self.async_update_reload_and_abort(
                    entry,
                    options={**entry.options, CONF_API_KEY: user_input[CONF_API_KEY]},
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_API_KEY): str}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> GoveeLanOptionsFlow:
        """Return the options flow."""
        return GoveeLanOptionsFlow()


class GoveeLanOptionsFlow(OptionsFlowWithReload):
    """Change or remove the API key."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the key form."""
        errors: dict[str, str] = {}
        if user_input is not None:
            api_key = user_input.get(CONF_API_KEY)
            if api_key:
                errors = await _async_validate(self.hass, api_key)
            if not errors:
                return self.async_create_entry(
                    data={CONF_API_KEY: api_key} if api_key else {}
                )
        current = self.config_entry.options.get(
            CONF_API_KEY, self.config_entry.data.get(CONF_API_KEY)
        )
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_API_KEY,
                    description={"suggested_value": current} if current else None,
                ): str
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
