"""Fixtures. All device IDs and keys here are synthetic."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from govee_local_api.light_capabilities import (
    GoveeLightFeatures,
    GoveeTemperatureRange,
)
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.govee_lan.cloud import CloudDevice, CloudEffect, CloudState
from custom_components.govee_lan.const import DOMAIN

LAN_ID = "AA:BB:CC:DD:EE:FF:00:01"
CLOUD_ID = "AA:BB:CC:DD:EE:FF:00:02"
API_KEY = "test-key"


@pytest.fixture(autouse=True)
def _enable_custom(enable_custom_integrations):
    """Let HA load the integration under test."""


def caps(min_k: int = 2200, max_k: int = 6500) -> dict:
    """Return capabilities for a typical RGBIC light."""
    return {
        "powerSwitch": {"instance": "powerSwitch"},
        "brightness": {"instance": "brightness"},
        "colorRgb": {"instance": "colorRgb"},
        "colorTemperatureK": {
            "instance": "colorTemperatureK",
            "parameters": {"range": {"min": min_k, "max": max_k}},
        },
        "lightScene": {"instance": "lightScene"},
    }


class FakeLanDevice:
    """Stands in for govee_local_api.GoveeDevice, with its public surface."""

    def __init__(self, fingerprint: str = LAN_ID, sku: str = "H6076") -> None:
        """Initialize a lit, freshly seen device."""
        self.fingerprint = fingerprint
        self.sku = sku
        self.ip = "192.0.2.10"
        self.on = True
        self.brightness = 50
        self.rgb_color = (255, 255, 255)
        self.temperature_color = 3000
        self.lastseen: datetime = dt_util.utcnow()
        self.capabilities = SimpleNamespace(
            features=GoveeLightFeatures.COLOR_RGB
            | GoveeLightFeatures.COLOR_KELVIN_TEMPERATURE
            | GoveeLightFeatures.BRIGHTNESS
        )
        # The library's table is narrower than the cloud's for this model.
        self.temperature_range = GoveeTemperatureRange(2700, 6500)
        self.transport = MagicMock()
        self.transport.is_closing.return_value = False
        self.turn_on = AsyncMock()
        self.turn_off = AsyncMock()
        self.set_brightness = AsyncMock()
        self.set_rgb_color = AsyncMock()
        self.set_temperature = AsyncMock()
        self.callback = None

    def set_update_callback(self, callback) -> None:
        """Record the callback."""
        self.callback = callback


class FakeController:
    """Stands in for GoveeController: no sockets, discovery driven by tests."""

    instances: list[FakeController] = []

    def __init__(self, **kwargs) -> None:
        """Capture the discovery callback."""
        self.discovered_callback = kwargs["discovered_callback"]
        self.protocols = [object()]
        self.send_update_message = MagicMock()
        self.start_error: OSError | None = None
        FakeController.instances.append(self)

    async def start(self, *, require_all: bool = True) -> None:
        """Pretend to bind."""

    def cleanup(self):
        """Pretend to close."""
        event = SimpleNamespace(wait=AsyncMock())
        return event


@pytest.fixture
def controller():
    """Patch in the fake LAN controller and return the class."""
    FakeController.instances = []
    with patch(
        "custom_components.govee_lan.coordinator.GoveeController", FakeController
    ):
        yield FakeController


@pytest.fixture
def adapters():
    """Provide one enabled network adapter."""
    with patch(
        "homeassistant.components.network.async_get_adapters",
        AsyncMock(
            return_value=[
                {
                    "name": "eth0",
                    "enabled": True,
                    "ipv4": [{"address": "192.0.2.1", "network_prefix": 24}],
                }
            ]
        ),
    ):
        yield


def online_state(**overrides) -> CloudState:
    """Return a cloud state for a reachable light."""
    values = {
        "online": True,
        "on": False,
        "brightness_pct": 65,
        "rgb": (255, 0, 0),
        "kelvin": 0,
    }
    return CloudState(**{**values, **overrides})


@pytest.fixture
def cloud():
    """Patch in a mock cloud client listing one light on an account."""
    device = CloudDevice(CLOUD_ID, "H619D", "Pro Strip", caps(2000, 9000))
    client = MagicMock()
    client.async_get_lights = AsyncMock(return_value=[device])
    client.async_get_state = AsyncMock(return_value=online_state())
    client.async_get_effects = AsyncMock(
        return_value={"Aurora": CloudEffect("lightScene", {"id": 1, "paramId": 2})}
    )
    client.async_control = AsyncMock()
    with patch(
        "custom_components.govee_lan.coordinator.GoveeCloud", return_value=client
    ):
        yield client


@pytest.fixture
def entry():
    """Return an entry shaped like the existing install: key in options."""
    return MockConfigEntry(
        domain=DOMAIN, title="govee_lan", data={}, options={"api_key": API_KEY}
    )
