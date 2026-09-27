"""Probing whether a cycle's d-TPP and NASR files are posted yet."""

import requests

from odp_kml import dtpp, nasr
from odp_kml.availability import is_available
from odp_kml.cycle import Cycle

CYCLE = Cycle.from_iso("2026-10-01")


class _Response:
    def __init__(self, status_code: int):
        self.status_code = status_code

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class _FakeSession:
    """Answers every probe with 206 except the URLs given other outcomes."""

    def __init__(self, outcomes: dict[str, int | Exception] | None = None):
        self.outcomes = outcomes or {}
        self.probes: list[tuple[str, dict]] = []

    def get(self, url, *, headers, **kwargs):
        self.probes.append((url, headers))
        outcome = self.outcomes.get(url, 206)
        if isinstance(outcome, Exception):
            raise outcome
        return _Response(outcome)


def test_available_when_every_probe_succeeds():
    session = _FakeSession()

    assert is_available(CYCLE, session=session)
    urls = {url for url, _ in session.probes}
    assert dtpp.metafile_url(CYCLE) in urls
    assert dtpp.to_pdf_url(CYCLE, "SW4") in urls
    assert nasr.group_url(CYCLE, "HPF") in urls
    assert len(urls) == 1 + 26 + 4
    assert all(headers == {"Range": "bytes=0-0"} for _, headers in session.probes)


def test_a_full_200_response_counts_as_available():
    session = _FakeSession({dtpp.metafile_url(CYCLE): 200})

    assert is_available(CYCLE, session=session)


def test_unavailable_when_one_file_is_missing():
    session = _FakeSession({dtpp.to_pdf_url(CYCLE, "PAC"): 404})

    assert not is_available(CYCLE, session=session)


def test_unavailable_when_a_probe_raises():
    failure = requests.ConnectionError("unreachable")
    session = _FakeSession({nasr.group_url(CYCLE, "APT"): failure})

    assert not is_available(CYCLE, session=session)
