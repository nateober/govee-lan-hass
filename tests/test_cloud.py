"""Tests for parsing what the Govee cloud returns."""

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.govee_lan.cloud import (
    CloudDevice,
    GoveeAuthError,
    GoveeCloud,
    GoveeCloudError,
    GoveeRateLimited,
    parse_state,
)

BASE = "https://openapi.api.govee.com/router/api/v1"


def _cap(instance, value):
    return {"instance": instance, "state": {"value": value}}


def test_parse_state_handles_empty_strings_and_rgb() -> None:
    """Unset capabilities arrive as '' and colors as one integer."""
    state = parse_state(
        {
            "capabilities": [
                _cap("online", True),
                _cap("powerSwitch", 1),
                _cap("brightness", 22),
                _cap("colorRgb", 0x102030),
                _cap("colorTemperatureK", ""),
                _cap("lightScene", ""),
            ]
        }
    )
    assert state.on and state.online
    assert state.brightness_pct == 22
    assert state.rgb == (0x10, 0x20, 0x30)
    assert state.kelvin == 0


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(False, False), ("false", False), ("true", True), ("", None)],
)
def test_parse_state_online_variants(raw, expected) -> None:
    """The string 'false' must not read as online."""
    assert parse_state({"capabilities": [_cap("online", raw)]}).online is expected


async def test_list_skips_groups(hass: HomeAssistant, aioclient_mock) -> None:
    """Govee group devices have no state and must not become lights."""
    aioclient_mock.get(
        f"{BASE}/user/devices",
        json={
            "code": 200,
            "data": [
                {
                    "sku": "H6076",
                    "device": "AA:01",
                    "deviceName": "Lamp",
                    "type": "devices.types.light",
                    "capabilities": [{"instance": "powerSwitch"}],
                },
                {"sku": "SameModeGroup", "device": "123", "capabilities": []},
            ],
        },
    )
    cloud = GoveeCloud(async_get_clientsession(hass), "k")
    lights = await cloud.async_get_lights()
    assert [light.device_id for light in lights] == ["AA:01"]


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (401, GoveeAuthError),
        (429, GoveeRateLimited),
        (500, GoveeCloudError),
    ],
)
async def test_http_errors(hass: HomeAssistant, aioclient_mock, status, error) -> None:
    """HTTP failures map to distinct errors."""
    aioclient_mock.get(f"{BASE}/user/devices", status=status)
    with pytest.raises(error):
        await GoveeCloud(async_get_clientsession(hass), "k").async_get_lights()


async def test_effects_combine_all_sources(hass: HomeAssistant, aioclient_mock) -> None:
    """Scenes, DIY scenes and snapshots all become effects."""

    def payload(instance, name, value):
        return {
            "code": 200,
            "payload": {
                "capabilities": [
                    {
                        "instance": instance,
                        "parameters": {"options": [{"name": name, "value": value}]},
                    }
                ]
            },
        }

    aioclient_mock.post(
        f"{BASE}/device/scenes", json=payload("lightScene", "Aurora", {"id": 1})
    )
    aioclient_mock.post(
        f"{BASE}/device/diy-scenes", json=payload("diyScene", "Mine", 7)
    )
    device = CloudDevice(
        "AA:01",
        "H6076",
        "Lamp",
        {
            "lightScene": {"instance": "lightScene"},
            "diyScene": {"instance": "diyScene"},
            "snapshot": {
                "instance": "snapshot",
                "parameters": {"options": [{"name": "Evening", "value": 9}]},
            },
        },
    )
    effects = await GoveeCloud(async_get_clientsession(hass), "k").async_get_effects(
        device
    )
    assert set(effects) == {"Aurora", "DIY: Mine", "Snapshot: Evening"}
    assert effects["DIY: Mine"].instance == "diyScene"
