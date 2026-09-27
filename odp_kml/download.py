"""HTTP download with on-disk caching."""

from __future__ import annotations

import logging
import urllib.parse
from pathlib import Path

from . import http

LOG = logging.getLogger(__name__)


def _cache_name(url: str) -> str:
    """Build a filesystem-safe cache filename from a URL."""
    return urllib.parse.quote(url, safe="")[:240]


def download(url: str, cache_dir: Path) -> Path:
    """Download url to cache_dir (skipping if already present). Returns path."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    out = cache_dir / _cache_name(url)
    if out.exists() and out.stat().st_size > 0:
        return out
    LOG.info("downloading %s", url)
    with http.get(url, stream=True) as r:
        r.raise_for_status()
        tmp = out.with_suffix(out.suffix + ".part")
        with open(tmp, "wb") as f:
            f.writelines(r.iter_content(1 << 20))
        tmp.rename(out)
    return out
