"""Tests for the config, reauth and options flows."""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.govee_lan.cloud import GoveeAuthError, GoveeCloudError
from custom_components.govee_lan.const import DOMAIN

VALIDATE = "custom_components.govee_lan.config_flow.GoveeCloud.async_get_lights"


@pytest.fixture(autouse=True)
def _no_setup():
    with patch("custom_components.govee_lan.async_setup_entry", return_value=True):
        yield


async def test_lan_only_needs_no_key(hass: HomeAssistant) -> None:
    """Setup works with no key."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"] == {}


@pytest.mark.parametrize(
    ("error", "code"),
    [(GoveeAuthError("x"), "invalid_auth"), (GoveeCloudError("x"), "cannot_connect")],
)
async def test_bad_key_shows_error(hass: HomeAssistant, error, code) -> None:
    """A key is tried before it is saved."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    with patch(VALIDATE, AsyncMock(side_effect=error)):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"api_key": "bad"}
        )
    assert result["errors"] == {"base": code}


async def test_reauth_replaces_key(hass: HomeAssistant, entry) -> None:
    """Reauth saves the new key where the entry keeps it."""
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    with patch(VALIDATE, AsyncMock(return_value=[])):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"api_key": "new"}
        )
    assert result["reason"] == "reauth_successful"
    assert entry.options["api_key"] == "new"


async def test_options_can_remove_key(hass: HomeAssistant, entry) -> None:
    """An empty key means LAN only."""
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {}
