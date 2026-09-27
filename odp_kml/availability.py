"""Whether a cycle's d-TPP and NASR files are posted yet.

The FAA posts an upcoming cycle's files some days before it takes effect.
CI builds that cycle as a prerelease only once every file the build
downloads answers a one-byte ranged GET.
"""

from __future__ import annotations

from collections.abc import Iterator
from http import HTTPStatus

import requests

from . import dtpp, http, nasr
from .cycle import Cycle

TO_VOLUMES = (
    "AK",
    *(f"EC{n}" for n in range(1, 4)),
    *(f"NC{n}" for n in range(1, 4)),
    *(f"NE{n}" for n in range(1, 5)),
    "NW1",
    "PAC",
    *(f"SC{n}" for n in range(1, 6)),
    *(f"SE{n}" for n in range(1, 5)),
    *(f"SW{n}" for n in range(1, 5)),
)
NASR_GROUPS = ("APT", "NAV", "FIX", "HPF")
PROBE_TIMEOUT = (5, 15)
_FIRST_BYTE = {"Range": "bytes=0-0"}
_POSTED = frozenset({HTTPStatus.OK, HTTPStatus.PARTIAL_CONTENT})


def is_available(cycle: Cycle, *, session: requests.Session = http.SESSION) -> bool:
    """True when the metafile, all 26 Takeoff-Minimums volumes and the four
    NASR zips for ``cycle`` are all posted."""
    return all(_is_posted(url, session) for url in _cycle_urls(cycle))


def _cycle_urls(cycle: Cycle) -> Iterator[str]:
    yield dtpp.metafile_url(cycle)
    for volume in TO_VOLUMES:
        yield dtpp.to_pdf_url(cycle, volume)
    for group in NASR_GROUPS:
        yield nasr.group_url(cycle, group)


def _is_posted(url: str, session: requests.Session) -> bool:
    try:
        with session.get(url, headers=_FIRST_BYTE, timeout=PROBE_TIMEOUT) as response:
            return response.status_code in _POSTED
    except requests.RequestException:
        return False
