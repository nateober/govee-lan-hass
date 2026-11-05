# Govee LAN Control for Home Assistant

Local-first control of Govee lights. A light that answers on your LAN is controlled
over the LAN. Anything else goes through Govee's cloud API, if you give it a key.

Maintained fork of [wez/govee-lan-hass](https://github.com/wez/govee-lan-hass), rebuilt
on the maintained [govee-local-api](https://github.com/Galorhallen/govee-local-api)
library and Govee's current Platform API. It upgrades 1.0.0 installs in place. See
[CHANGELOG.md](CHANGELOG.md) and [docs/design.md](docs/design.md).

If all your lights have LAN Control and you don't need cloud scenes, use Home Assistant's
built-in `govee_light_local` instead.

## Install

Add `nateober/govee-lan-hass` as a custom Integration repository in the Marketplace
(HACS before Home Assistant 2026.11), download it and restart. Then add **Govee LAN
Control** under Settings > Devices & Services.

The API key is optional. Request one in the Govee app under Profile > Settings > Apply
for API Key. Without it, only LAN lights work, with their model number as a name and no
effects.

Enable **LAN Control** per device in the Govee app. If the option is missing, update the
firmware, kill the app and reopen it with your phone's Wi-Fi off.

Switching from `wez/govee-lan-hass` in the Marketplace: it won't uninstall a repository
that is still set up, or install over a domain another repository owns. Note your API key,
delete the integration under Settings > Devices & services, uninstall wez's, download this
one, restart and add the integration again. Home Assistant restores entity IDs, names and
areas for 30 days after deletion.

## Network requirements

Home Assistant must share a network with the lights and multicast must work over Wi-Fi.
Discovery goes to 239.255.255.250:4001, replies come back on UDP 4002 and commands go to
UDP 4003. Only one Govee LAN implementation can use port 4002 per IP address, so this
can't run alongside `govee_light_local` or govee2mqtt.

## Attributes

- `lan_enabled`: answered on the LAN in the last 60 seconds
- `http_enabled`: listed by the cloud API
- `cloud_online`: Govee's own view of whether the light is connected
- `ip_address`: last LAN address

## Troubleshooting

Settings > Devices & Services > Govee LAN Control > Download diagnostics shows how each
light is being reached, with keys and device IDs left out. For logs:

```yaml
logger:
  logs:
    custom_components.govee_lan: debug
    govee_local_api: debug
```

## Development

```bash
uv venv --python 3.14 && uv pip install -r requirements_test.txt
.venv/bin/ruff check . && .venv/bin/pytest
```
