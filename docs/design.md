# Design notes

## Prior art

- `govee_light_local` (HA core) is LAN only, with a maintained library that tracks
  new models. `govee-local-api` is reused here for that reason.
- `govee2mqtt` is wez's successor. It needs an MQTT broker and is a separate app with
  its own entities. It cannot share UDP port 4002 with this integration.
- Forks of wez/govee-lan-hass (Blacktime91, LazyAdmin22, PR #87) patch imports but
  stay on the 2023 library and the legacy API.

## Where this departs from them

Core is the right answer if every light has LAN Control and effects don't matter.
This integration exists for mixed setups: lights that only the cloud can reach, and
cloud scenes, under one entity per light.

## Routing

A light answering on the LAN within 60 s is controlled and read over the LAN. Otherwise
it goes through the cloud, polled every 2 minutes against the 10,000 requests/day limit.

A light is available if it answers on the LAN or the cloud reports `online: true`.
Reports exist of Govee's `online` being wrong for some lights
(developer.govee.com/discuss/68362d4a68f4930018519997), so a LAN answer always wins.

## Color temperature

`device.set_temperature()` clamps to the library's per-model table, which disagrees
with the cloud for at least H6076 (2700 K against 2200 K). Kelvin writes go through
`device.transport` with the range the cloud declares, so warm-white automations don't
shift. Remove this if the library takes the range as an argument.

## Staying current

`.github/workflows/ci.yml` runs hassfest, HACS validation and the tests weekly against
the newest Home Assistant test harness, with deprecations from this package as errors.
`govee-local-api` is a minimum version, not a pin, because Home Assistant core depends
on it too (hassfest rejects a pin) and this needs to follow whatever core ships.
