"""Constants for Govee LAN Control."""

from datetime import timedelta
from typing import Final

DOMAIN: Final = "govee_lan"
MANUFACTURER: Final = "Govee"

# LAN status requests are cheap UDP datagrams.
LAN_POLL_INTERVAL: Final = timedelta(seconds=10)
# A LAN device that has not answered for this long is routed via the cloud.
LAN_TIMEOUT: Final = timedelta(seconds=60)

# Govee allows 10,000 cloud requests a day per key. Polling every 2 minutes
# costs 720 a day per cloud-routed light, so a dozen lights still fit.
CLOUD_STATE_INTERVAL: Final = 120.0
CLOUD_LIST_INTERVAL: Final = 600.0
CLOUD_SCENE_INTERVAL: Final = 6 * 3600.0
CLOUD_RATE_LIMIT_BACKOFF: Final = 900.0
CLOUD_TIMEOUT: Final = 10.0
# Consecutive failed state reads before a cloud-routed light is unavailable.
CLOUD_MAX_FAILURES: Final = 3

# Both transports can report the old state just after a write, so reads are
# ignored for this long and the requested state is shown instead.
LAN_WRITE_HOLD: Final = 3.0
CLOUD_WRITE_HOLD: Final = 30.0

DEFAULT_MIN_KELVIN: Final = 2000
DEFAULT_MAX_KELVIN: Final = 9000
