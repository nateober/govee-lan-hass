"""Tests for setup, routing and the behaviors the old version got wrong."""

import asyncio
import json
import time
from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.components.light import DOMAIN as LIGHT_DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.govee_lan.cloud import GoveeAuthError, GoveeRateLimited
from custom_components.govee_lan.const import DOMAIN

from .conftest import CLOUD_ID, LAN_ID, FakeLanDevice, online_state

CLOUD_ENTITY = "light.pro_strip"
CLOUD_UNIQUE_ID = "H619D_" + CLOUD_ID.replace(":", "")


async def setup(hass, entry):
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def call(hass, service, entity_id, **data):
    await hass.services.async_call(
        LIGHT_DOMAIN, service, {"entity_id": entity_id, **data}, blocking=True
    )
    await hass.async_block_till_done()


def discover(hass, controller, device) -> str:
    controller.instances[-1].discovered_callback(device, True)
    device.callback(device)
    return (
        f"light.{device.sku.lower()}_{device.fingerprint.replace(':', '')[-4:].lower()}"
    )


@pytest.fixture(autouse=True)
def _base(adapters, controller):
    """Every test runs on the fake network."""


async def test_existing_entity_ids_survive(hass: HomeAssistant, entry, cloud) -> None:
    """The upgrade must adopt existing registry entries, not create new ones."""
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    old = registry.async_get_or_create(
        LIGHT_DOMAIN,
        DOMAIN,
        CLOUD_UNIQUE_ID,
        suggested_object_id="my_custom_id",
        config_entry=entry,
    )
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert registry.async_get(old.entity_id).unique_id == CLOUD_UNIQUE_ID
    assert hass.states.get("light.my_custom_id").state == STATE_OFF
    assert len(er.async_entries_for_config_entry(registry, entry.entry_id)) == 1


async def test_offline_light_is_unavailable_not_on(
    hass: HomeAssistant, entry, cloud
) -> None:
    """An unplugged light was reported as on. It must be unavailable."""
    cloud.async_get_state.return_value = online_state(online=False, on=True)
    await setup(hass, entry)
    assert hass.states.get(CLOUD_ENTITY).state == STATE_UNAVAILABLE


async def test_lan_answer_beats_cloud_offline(
    hass: HomeAssistant, entry, cloud, controller
) -> None:
    """Answering on the LAN is proof of life whatever the cloud says."""
    cloud.async_get_state.return_value = online_state(online=False)
    await setup(hass, entry)
    device = FakeLanDevice(fingerprint=CLOUD_ID, sku="H619D")
    controller.instances[-1].discovered_callback(device, True)
    device.callback(device)
    await hass.async_block_till_done()
    assert hass.states.get(CLOUD_ENTITY).state == STATE_ON


async def test_silent_lan_light_goes_unavailable(
    hass: HomeAssistant, entry, cloud, controller
) -> None:
    """A LAN-only light that stops answering must not stay available."""
    cloud.async_get_lights.return_value = []
    await setup(hass, entry)
    device = FakeLanDevice()
    entity_id = discover(hass, controller, device)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == STATE_ON

    device.lastseen = dt_util.utcnow() - timedelta(minutes=5)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=11))
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE


async def test_lan_route_uses_no_cloud_requests(
    hass: HomeAssistant, entry, cloud, controller
) -> None:
    """Commands to a LAN light stay local."""
    cloud.async_get_lights.return_value = []
    await setup(hass, entry)
    device = FakeLanDevice()
    entity_id = discover(hass, controller, device)
    await hass.async_block_till_done()

    await call(hass, "turn_on", entity_id, brightness=255, rgb_color=(1, 2, 3))
    device.set_brightness.assert_awaited_once_with(100)
    device.set_rgb_color.assert_awaited_once_with(1, 2, 3)
    device.turn_on.assert_not_awaited()
    cloud.async_control.assert_not_awaited()


async def test_lan_kelvin_uses_cloud_range_not_library_table(
    hass: HomeAssistant, entry, cloud, controller
) -> None:
    """A warm-white request must not be pushed up to the library's 2700 K."""
    cloud.async_get_lights.return_value = [
        _lan_cloud_device(),
    ]
    await setup(hass, entry)
    device = FakeLanDevice()
    entity_id = "light.lamp"
    controller.instances[-1].discovered_callback(device, True)
    device.callback(device)
    await hass.async_block_till_done()

    await call(hass, "turn_on", entity_id, color_temp_kelvin=2000)
    sent = json.loads(device.transport.sendto.call_args[0][0])
    assert sent["msg"]["data"]["colorTemInKelvin"] == 2200
    device.set_temperature.assert_not_awaited()


def _lan_cloud_device():
    from custom_components.govee_lan.cloud import CloudDevice

    from .conftest import caps

    return CloudDevice(LAN_ID, "H6076", "Lamp", caps(2200, 6500))


async def test_write_wakes_a_light_that_is_off(
    hass: HomeAssistant, entry, cloud, controller
) -> None:
    """Brightness alone must not leave an off light dark."""
    cloud.async_get_lights.return_value = []
    await setup(hass, entry)
    device = FakeLanDevice()
    device.on = False
    entity_id = discover(hass, controller, device)
    await hass.async_block_till_done()

    await call(hass, "turn_on", entity_id, brightness=128)
    device.turn_on.assert_awaited_once()
    assert hass.states.get(entity_id).state == STATE_ON


async def test_stale_read_after_write_is_ignored(
    hass: HomeAssistant, entry, cloud, controller
) -> None:
    """Lights echo the old state just after a write. It must not flicker back."""
    cloud.async_get_lights.return_value = []
    await setup(hass, entry)
    device = FakeLanDevice()
    entity_id = discover(hass, controller, device)
    await hass.async_block_till_done()

    await call(hass, "turn_off", entity_id)
    device.callback(device)  # still reports on
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == STATE_OFF

    with patch(
        "custom_components.govee_lan.coordinator.time.monotonic",
        return_value=time.monotonic() + 60,
    ):
        device.callback(device)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == STATE_ON


async def test_cloud_route_and_brightness_floor(
    hass: HomeAssistant, entry, cloud
) -> None:
    """Off cloud lights get power first and brightness never rounds to zero."""
    await setup(hass, entry)
    await call(hass, "turn_on", CLOUD_ENTITY, brightness=1)
    sent = [c.args[2:] for c in cloud.async_control.await_args_list]
    assert sent == [("powerSwitch", 1), ("brightness", 1)]


async def test_cloud_failure_raises(hass: HomeAssistant, entry, cloud) -> None:
    """A failed command is reported to the caller, not swallowed."""
    await setup(hass, entry)
    cloud.async_control.side_effect = GoveeRateLimited("HTTP 429")
    with pytest.raises(HomeAssistantError):
        await call(hass, "turn_on", CLOUD_ENTITY)
    assert hass.states.get(CLOUD_ENTITY).state == STATE_OFF


async def test_effect_set_and_cleared_by_color(
    hass: HomeAssistant, entry, cloud
) -> None:
    """Effects apply through the cloud and a color replaces them."""
    await setup(hass, entry)
    state = hass.states.get(CLOUD_ENTITY)
    assert state.attributes["effect_list"] == ["Aurora"]

    await call(hass, "turn_on", CLOUD_ENTITY, effect="Aurora")
    assert hass.states.get(CLOUD_ENTITY).attributes["effect"] == "Aurora"
    assert cloud.async_control.await_args.args[2:] == (
        "lightScene",
        {"id": 1, "paramId": 2},
    )

    await call(hass, "turn_on", CLOUD_ENTITY, rgb_color=(1, 2, 3))
    assert hass.states.get(CLOUD_ENTITY).attributes.get("effect") is None


async def test_old_scene_effect_none_does_not_fail(
    hass: HomeAssistant, entry, cloud
) -> None:
    """scenes.yaml snapshots replay effect: None, which is not a real effect."""
    await setup(hass, entry)
    await call(hass, "turn_on", CLOUD_ENTITY, effect="None")
    assert hass.states.get(CLOUD_ENTITY).state == STATE_ON
    instances = [c.args[2] for c in cloud.async_control.await_args_list]
    assert instances == ["powerSwitch"]


async def test_cloud_outage_does_not_block_setup_or_lan(
    hass: HomeAssistant, entry, cloud, controller
) -> None:
    """LAN control must not depend on Govee's servers."""
    from custom_components.govee_lan.cloud import GoveeCloudError

    cloud.async_get_lights.side_effect = GoveeCloudError("down")
    await setup(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    device = FakeLanDevice()
    entity_id = discover(hass, controller, device)
    await hass.async_block_till_done()
    await call(hass, "turn_off", entity_id)
    device.turn_off.assert_awaited_once()


async def test_rejected_key_starts_reauth(hass: HomeAssistant, entry, cloud) -> None:
    """A dead key asks for a new one."""
    cloud.async_get_lights.side_effect = GoveeAuthError("HTTP 401")
    await setup(hass, entry)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert [f["step_id"] for f in hass.config_entries.flow.async_progress()] == [
        "reauth_confirm"
    ]


async def test_rate_limit_pauses_cloud_polling(
    hass: HomeAssistant, entry, cloud
) -> None:
    """A 429 backs off rather than burning more requests."""
    await setup(hass, entry)
    cloud.async_get_state.reset_mock()
    cloud.async_get_lights.side_effect = GoveeRateLimited("HTTP 429")
    coordinator = entry.runtime_data
    coordinator._next_list = 0
    await coordinator._async_cloud_pass()
    assert coordinator._cloud_paused_until > time.monotonic()
    coordinator._next_list = 0
    cloud.async_get_lights.reset_mock()
    await coordinator._async_cloud_pass()
    cloud.async_get_lights.assert_not_awaited()


async def test_unload(hass: HomeAssistant, entry, cloud) -> None:
    """The entry unloads cleanly."""
    await setup(hass, entry)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_turn_on_queued_behind_turn_off_sends_power(
    hass: HomeAssistant, entry, cloud, controller
) -> None:
    """Power is decided after the lock, so an in-flight off is seen."""
    cloud.async_get_lights.return_value = []
    await setup(hass, entry)
    device = FakeLanDevice()
    entity_id = discover(hass, controller, device)
    await hass.async_block_till_done()
    light = entry.runtime_data.lights[LAN_ID]

    async with light.lock:
        task = hass.async_create_task(
            entry.runtime_data.async_turn_on(light, brightness_pct=40)
        )
        await asyncio.sleep(0)  # let it queue on the lock
        await device.turn_off()
        light.state.on = False
    await task
    device.turn_on.assert_awaited_once()
    assert hass.states.get(entity_id) is not None


async def test_closed_socket_falls_back_to_library(
    hass: HomeAssistant, entry, cloud, controller
) -> None:
    """A replaced socket must not swallow a color temperature write."""
    cloud.async_get_lights.return_value = []
    await setup(hass, entry)
    device = FakeLanDevice()
    device.transport.is_closing.return_value = True
    entity_id = discover(hass, controller, device)
    await hass.async_block_till_done()
    await call(hass, "turn_on", entity_id, color_temp_kelvin=3000)
    device.set_temperature.assert_awaited_once_with(3000)
    device.transport.sendto.assert_not_called()


async def test_state_without_online_field_is_available(
    hass: HomeAssistant, entry, cloud
) -> None:
    """A good read that omits online must not hide the light forever."""
    cloud.async_get_state.return_value = online_state(online=None)
    await setup(hass, entry)
    assert hass.states.get(CLOUD_ENTITY).state == STATE_OFF


async def test_discovery_requests_status_after_library_registers(
    hass: HomeAssistant, entry, cloud, controller
) -> None:
    """The status request must run after the discovery callback returns."""
    cloud.async_get_lights.return_value = []
    await setup(hass, entry)
    fake = controller.instances[-1]
    fake.send_update_message.reset_mock()
    fake.discovered_callback(FakeLanDevice(), True)
    fake.send_update_message.assert_not_called()
    await hass.async_block_till_done()
    fake.send_update_message.assert_called_once()
