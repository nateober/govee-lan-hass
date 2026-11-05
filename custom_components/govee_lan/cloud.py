"""Client for Govee's cloud Platform API (the openapi "router" API)."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

import aiohttp

from .const import CLOUD_TIMEOUT

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://openapi.api.govee.com/router/api/v1"
LIGHT_TYPE = "devices.types.light"

CAP_ON_OFF = "devices.capabilities.on_off"
CAP_RANGE = "devices.capabilities.range"
CAP_COLOR = "devices.capabilities.color_setting"
CAP_SCENE = "devices.capabilities.dynamic_scene"

INSTANCE_POWER = "powerSwitch"
INSTANCE_BRIGHTNESS = "brightness"
INSTANCE_RGB = "colorRgb"
INSTANCE_KELVIN = "colorTemperatureK"
INSTANCE_LIGHT_SCENE = "lightScene"
INSTANCE_DIY_SCENE = "diyScene"
INSTANCE_SNAPSHOT = "snapshot"


class GoveeCloudError(Exception):
    """The cloud request failed."""


class GoveeAuthError(GoveeCloudError):
    """Govee rejected the API key."""


class GoveeRateLimited(GoveeCloudError):
    """The API key has run out of requests."""


@dataclass(frozen=True, slots=True)
class CloudState:
    """State reported by the cloud. ``online`` is None when it was not reported."""

    online: bool | None
    on: bool
    brightness_pct: int
    rgb: tuple[int, int, int]
    kelvin: int


@dataclass(frozen=True, slots=True)
class CloudEffect:
    """A selectable scene and the capability that activates it."""

    instance: str
    value: Any


@dataclass(slots=True)
class CloudDevice:
    """A light as listed by the cloud."""

    device_id: str
    sku: str
    name: str
    capabilities: dict[str, dict[str, Any]] = field(default_factory=dict)

    def has(self, instance: str) -> bool:
        """Return whether the device has this capability."""
        return instance in self.capabilities

    @property
    def kelvin_range(self) -> tuple[int, int] | None:
        """Return the color temperature range the device declares."""
        cap = self.capabilities.get(INSTANCE_KELVIN)
        value_range = (cap or {}).get("parameters", {}).get("range")
        if not value_range:
            return None
        return (value_range["min"], value_range["max"])

    @property
    def has_scenes(self) -> bool:
        """Return whether the device has scenes worth fetching."""
        return self.has(INSTANCE_LIGHT_SCENE) or self.has(INSTANCE_DIY_SCENE)

    @property
    def snapshots(self) -> dict[str, CloudEffect]:
        """Return the snapshots, which are listed inline with the device."""
        return _parse_options(self.capabilities.get(INSTANCE_SNAPSHOT), "Snapshot: ")


def _parse_options(capability: dict[str, Any] | None, prefix: str = "") -> dict:
    """Turn a dynamic_scene capability into a name -> effect mapping."""
    if not capability:
        return {}
    instance = capability.get("instance", "")
    effects: dict[str, CloudEffect] = {}
    for option in capability.get("parameters", {}).get("options", []):
        name = option.get("name")
        value = option.get("value")
        if name and value not in (None, ""):
            effects[f"{prefix}{name}"] = CloudEffect(instance, value)
    return effects


def _as_bool(value: Any) -> bool | None:
    """Read ``online``, which Govee may send as a bool or a string."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in ("true", "false"):
        return value.lower() == "true"
    return None


def _as_int(value: Any) -> int:
    """Read a numeric state, which Govee sends as an empty string when unset."""
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def parse_state(payload: dict[str, Any]) -> CloudState:
    """Parse a /device/state payload."""
    values = {
        cap.get("instance"): cap.get("state", {}).get("value")
        for cap in payload.get("capabilities", [])
    }
    rgb = _as_int(values.get(INSTANCE_RGB))
    return CloudState(
        online=_as_bool(values.get("online")),
        on=_as_int(values.get(INSTANCE_POWER)) == 1,
        brightness_pct=_as_int(values.get(INSTANCE_BRIGHTNESS)),
        rgb=((rgb >> 16) & 0xFF, (rgb >> 8) & 0xFF, rgb & 0xFF),
        kelvin=_as_int(values.get(INSTANCE_KELVIN)),
    )


class GoveeCloud:
    """Talks to the Govee Platform API using Home Assistant's shared session."""

    def __init__(self, session: aiohttp.ClientSession, api_key: str) -> None:
        """Initialize the client."""
        self._session = session
        self._api_key = api_key

    async def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        body = None
        if payload is not None:
            body = {"requestId": str(uuid.uuid4()), "payload": payload}
        try:
            async with self._session.request(
                method,
                f"{BASE_URL}{path}",
                headers={"Govee-API-Key": self._api_key},
                json=body,
                timeout=aiohttp.ClientTimeout(total=CLOUD_TIMEOUT),
            ) as resp:
                if resp.status in (401, 403):
                    raise GoveeAuthError(f"HTTP {resp.status}")
                if resp.status == 429:
                    raise GoveeRateLimited("HTTP 429")
                if resp.status != 200:
                    raise GoveeCloudError(f"HTTP {resp.status}")
                data = await resp.json(content_type=None)
        except (TimeoutError, aiohttp.ClientError, ValueError) as err:
            raise GoveeCloudError(str(err) or type(err).__name__) from err

        code = data.get("code", 200)
        if code != 200:
            raise GoveeCloudError(f"{code}: {data.get('message') or data.get('msg')}")
        return data

    async def async_get_lights(self) -> list[CloudDevice]:
        """List the account's lights. Groups and non-lights are left out."""
        data = await self._request("GET", "/user/devices")
        lights = []
        for item in data.get("data", []):
            if item.get("type") != LIGHT_TYPE:
                _LOGGER.debug("Skipping non-light %s", item.get("sku"))
                continue
            lights.append(
                CloudDevice(
                    device_id=item["device"],
                    sku=item["sku"],
                    name=item.get("deviceName") or item["sku"],
                    capabilities={
                        cap["instance"]: cap for cap in item.get("capabilities", [])
                    },
                )
            )
        return lights

    async def async_get_state(self, device: CloudDevice) -> CloudState:
        """Read a light's state."""
        data = await self._request(
            "POST",
            "/device/state",
            {"sku": device.sku, "device": device.device_id},
        )
        return parse_state(data.get("payload", {}))

    async def async_get_effects(self, device: CloudDevice) -> dict[str, CloudEffect]:
        """Fetch every effect a light offers."""
        effects: dict[str, CloudEffect] = {}
        target = {"sku": device.sku, "device": device.device_id}
        if device.has(INSTANCE_LIGHT_SCENE):
            data = await self._request("POST", "/device/scenes", target)
            for cap in data.get("payload", {}).get("capabilities", []):
                effects.update(_parse_options(cap))
        if device.has(INSTANCE_DIY_SCENE):
            data = await self._request("POST", "/device/diy-scenes", target)
            for cap in data.get("payload", {}).get("capabilities", []):
                effects.update(_parse_options(cap, "DIY: "))
        effects.update(device.snapshots)
        return effects

    async def async_control(
        self, device: CloudDevice, cap_type: str, instance: str, value: Any
    ) -> None:
        """Send one capability change."""
        data = await self._request(
            "POST",
            "/device/control",
            {
                "sku": device.sku,
                "device": device.device_id,
                "capability": {
                    "type": cap_type,
                    "instance": instance,
                    "value": value,
                },
            },
        )
        status = data.get("capability", {}).get("state", {}).get("status")
        if status not in (None, "success"):
            raise GoveeCloudError(f"device reported {status}")
