"""Shared HTTP session with retries/backoff for flaky FAA endpoints.

The FAA hosts (www.faa.gov, nfdc.faa.gov, aeronav.faa.gov)
periodically time out or return 5xx, which would otherwise abort the whole
content-pack build on a single transient hiccup. Routing every request
through this session retries those failures with exponential backoff.
"""

from __future__ import annotations

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import HEADERS

# (connect, read): fail fast on a dead host, but tolerate slow large
# transfers. The previous bare 60s applied to both; the read budget here is
# generous because NASR zips are big and the FAA CDN is slow.
DEFAULT_TIMEOUT = (10, 120)

# Retries cover connect failures, read timeouts, and retryable status codes.
# backoff_factor=2 → sleeps of 0, 2, 4, 8, 16s between the 5 attempts.
_RETRY = Retry(
    total=5,
    connect=5,
    read=5,
    status=5,
    backoff_factor=2.0,
    status_forcelist=(429, 500, 502, 503, 504),
    allowed_methods=frozenset(["GET", "HEAD"]),
    raise_on_status=False,
)


def make_session() -> requests.Session:
    """Build a requests.Session with the retrying adapter and shared headers."""
    s = requests.Session()
    s.headers.update(HEADERS)
    adapter = HTTPAdapter(max_retries=_RETRY)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


# Module-level singleton: connection pooling plus one place to configure retries.
SESSION = make_session()


def get(
    url: str, *, stream: bool = False, timeout=DEFAULT_TIMEOUT
) -> requests.Response:
    """GET url through the retrying shared session."""
    return SESSION.get(url, stream=stream, timeout=timeout)
