# Changelog

## 2.0.0

Rebuilt from wez/govee-lan-hass 1.0.0, which is unmaintained. Domain, unique IDs,
device identifiers and config entry format are unchanged, so existing entities,
scenes and scripts carry over.

- LAN control now uses `govee-local-api`, the library behind Home Assistant's own
  `govee_light_local`, replacing `govee-led-wez` (last changed May 2023).
- Cloud control moved from the legacy `developer-api.govee.com/v1` API to the
  current Platform API, through Home Assistant's shared HTTP session. This removes
  the blocking SSL load on the event loop.
- Offline lights are unavailable. They used to show their last known state.
- Devices the legacy API did not list are now found.
- Color temperature limits come from each light. They were 2000-9000 K for all.
- Failed commands raise an error. They used to be logged at debug and look successful.
- A brightness or color change now turns on a light that is off.
- A Govee cloud outage or rate limit no longer affects LAN lights.
- Adds DIY scenes and snapshots as effects, API key validation, reauthentication,
  diagnostics, tests, and weekly CI against the latest Home Assistant.

Removed: Bluetooth control, the `ble_enabled`, `update_status` and `timeout_count`
attributes, and mired color temperature input, which HA itself no longer sends.
