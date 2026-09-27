"""Constants: HTTP headers, pack metadata, and display parameters."""

from __future__ import annotations

# Standard User-Agent; some FAA/CDN endpoints 403/503 on the default Python UA.
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0 Safari/537.36"
)
HEADERS = {"User-Agent": UA}

# --- Pack metadata (user-visible branding) ------------------------------------

PACK_NAME = "Textual ODPs"
PACK_ABBREV = "ODP"
ORGANIZATION = "Tim Morgan"
KML_FILENAME = "Textual ODPs.kml"

TEST_GRID_PACK_NAME = "ODP Test Grid"
TEST_GRID_PACK_ABBREV = "ODPTEST"
TEST_GRID_KML_FILENAME = "ODP Test Grid.kml"
