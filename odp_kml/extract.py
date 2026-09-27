"""One text block per airport from a d-TPP Takeoff-Minimums volume PDF.

``pdftotext -layout`` renders each born-digital page cleanly, but every page
carries furniture (page number, ``T TAKEOFF MINS`` banners, a cycle code, and
the effective-date line printed down the page's middle), entries broken by a
page end carry ``(CON’T)`` markers, and long sentences wrap at the column
edge with no hanging indent. This module removes the furniture, finds each
airport's heading, and hands each entry's lines to ``lines.join_lines``.
"""

from __future__ import annotations

import collections
import dataclasses
import itertools
import re
import subprocess
from collections.abc import Iterator, Mapping
from pathlib import Path

from .dtpp import AirportMeta
from .lines import canonical_title, is_amendment, join_lines

_PAGE_FURNITURE = re.compile(
    r"T TAKEOFF MINS\b.*T"
    r"|L\d+"
    r"|\d{5}(?:\s+L\d+)?"
    r"|\d{2} [A-Z]{3} \d{4} to \d{2} [A-Z]{3} \d{4}.*"
)
_CONTINUATION_MARK = re.compile(r"\s*\(?CON['’]T\b\)?")
_CITY_LINE = re.compile(r"(?P<city>[^a-z]+, [A-Z]{2})")
_AIRPORT_LINE = re.compile(
    r"(?P<name>[^a-z]+?)\s*\((?P<lid>[A-Z0-9]{3,4})\)(?:\s*\([A-Z0-9]{4}\))?"
)

# Looser than _AIRPORT_LINE: any line ending in ``(LID)`` or ``(LID) (ICAO)``.
_HEADING_CANDIDATE = re.compile(r".*\((?P<lid>[A-Z0-9]{3})\)(?:\s*\([A-Z0-9]{4}\))?\s*")
_PARENTHESIZED_ID = re.compile(r"\(([A-Z0-9]{3})\)")
_DESTINATION = re.compile(r'"\((?P<name>[^)]+)\)\d*"\s*$')
# Named destinations for the front matter's "(DPs)", "(DVA)" and "(ICA)".
FRONT_MATTER_DESTINATIONS = frozenset({"DPs", "DVA", "ICA"})
# pdfinfo names destinations only for three-character identifiers.
_DESTINATION_ID_LENGTH = 3


@dataclasses.dataclass(frozen=True)
class AirportBlock:
    """One airport's Takeoff-Minimums entry as logical lines of text."""

    lid: str
    city: str
    name: str
    text: str
    volume: str
    pages: tuple[int, ...]


@dataclasses.dataclass(frozen=True)
class _Line:
    page: int
    text: str


@dataclasses.dataclass
class _Entry:
    lid: str
    city: str
    name: str
    first: int
    end: int


@dataclasses.dataclass(frozen=True)
class _Heading:
    start: int
    city: str | None
    name: str
    lid: str


def pdf_text_pages(pdf: Path) -> list[str]:
    """The ``pdftotext -layout`` text of each page of ``pdf``."""
    text = _run("pdftotext", "-layout", "-enc", "UTF-8", str(pdf), "-")
    pages = text.split("\f")
    return pages[:-1] if pages and not pages[-1].strip() else pages


def named_destinations(pdf: Path) -> set[str]:
    """The PDF's named destinations: ``TPH`` for both ``(TPH)`` and ``(TPH)1``."""
    return {
        match["name"]
        for line in _run("pdfinfo", "-dests", str(pdf)).splitlines()
        if (match := _DESTINATION.search(line))
    }


def extract_blocks(pdf: Path, volume: str) -> tuple[list[AirportBlock], list[str]]:
    """Every airport block in a volume PDF, plus warnings about its structure.

    Content oddities never raise; a failing PDF tool does.
    """
    return blocks_from_pages(pdf_text_pages(pdf), named_destinations(pdf), volume)


def blocks_from_pages(
    pages: list[str], destinations: set[str], volume: str
) -> tuple[list[AirportBlock], list[str]]:
    """Airport blocks from a volume's page texts, plus warnings.

    Warnings name destinations with no block, blocks with no destination and
    duplicate LIDs.
    """
    lines = _without_repeated_headings(
        _merge_wrapped_titles(list(_content_lines(pages)))
    )
    blocks = [_block(entry, lines, volume) for entry in _entries(lines)]
    airport_destinations = (
        destinations - FRONT_MATTER_DESTINATIONS - _mentioned_only(lines)
    )
    return blocks, _warnings(blocks, airport_destinations, volume)


def check_against_metafile(
    blocks: list[AirportBlock], expected: Mapping[str, AirportMeta]
) -> list[str]:
    """Compare one volume's blocks with the metafile airports expected in it.

    ``expected`` maps LID to airport for that volume only. A block matches an
    airport when its printed id is the airport's LID or ICAO id, or is the
    LID with a leading ``K`` (``KW94`` for ``W94``).
    """
    aliases = _metafile_aliases(expected)
    matched = {
        aliases.get(identifier) for block in blocks for identifier in _ids_of(block)
    }
    return [
        *(
            f"{airport.volume}: metafile airport {lid} has no block"
            for lid, airport in expected.items()
            if lid not in matched
        ),
        *(
            f"{block.volume}: block {block.lid} has no metafile airport"
            for block in blocks
            if not any(identifier in aliases for identifier in _ids_of(block))
        ),
    ]


def _metafile_aliases(expected: Mapping[str, AirportMeta]) -> dict[str, str]:
    """Every id an airport may be printed under, mapped to its LID."""
    return {
        alias: lid
        for lid, airport in expected.items()
        for alias in (lid, airport.icao)
        if alias
    }


def _ids_of(block: AirportBlock) -> tuple[str, ...]:
    """The block's printed id, plus that id without a leading ``K``."""
    if len(block.lid) == 4 and block.lid.startswith("K"):
        return block.lid, block.lid[1:]
    return (block.lid,)


def _run(*command: str) -> str:
    return subprocess.run(
        command, capture_output=True, check=True, encoding="utf-8"
    ).stdout


def _content_lines(pages: list[str]) -> Iterator[_Line]:
    """Non-blank lines stripped of page furniture and continuation markers."""
    for number, page in enumerate(pages, start=1):
        for raw in page.split("\n"):
            text = raw.strip()
            if not text or _PAGE_FURNITURE.fullmatch(text):
                continue
            unmarked = _CONTINUATION_MARK.sub("", text)
            if unmarked and not (unmarked != text and _is_heading(unmarked)):
                yield _Line(number, unmarked)


def _merge_wrapped_titles(lines: list[_Line]) -> list[_Line]:
    """Rejoin a block title that wrapped onto a second line."""
    merged: list[_Line] = []
    for line in lines:
        if merged and canonical_title(f"{merged[-1].text} {line.text}") is not None:
            merged[-1] = _Line(merged[-1].page, f"{merged[-1].text} {line.text}")
        else:
            merged.append(line)
    return merged


def _without_repeated_headings(lines: list[_Line]) -> list[_Line]:
    """Drop a page's leading heading lines when no title or amendment follows.

    That is an entry's heading repeated at the top of a continuation page
    without its ``(CON’T)`` markers.
    """
    kept: list[_Line] = []
    for _, page in itertools.groupby(lines, key=lambda line: line.page):
        page_lines = list(page)
        count = _leading_heading_count(page_lines)
        repeated = count and (
            count == len(page_lines) or not _opens_block(page_lines[count].text)
        )
        kept.extend(page_lines[count:] if repeated else page_lines)
    return kept


def _leading_heading_count(page_lines: list[_Line]) -> int:
    return next(
        (i for i, line in enumerate(page_lines[:2]) if not _is_heading(line.text)),
        min(len(page_lines), 2),
    )


def _is_heading(text: str) -> bool:
    return bool(_CITY_LINE.fullmatch(text) or _airport_line(text))


def _airport_line(text: str) -> re.Match[str] | None:
    """``NAME (LID)`` or ``NAME (LID) (ICAO)``; amendment lines end the same way."""
    return None if is_amendment(text) else _AIRPORT_LINE.fullmatch(text)


def _opens_block(text: str) -> bool:
    return canonical_title(text) is not None or is_amendment(text)


def _entries(lines: list[_Line]) -> list[_Entry]:
    """Airport entries, each opened by its heading.

    A heading is recognized by what follows it: a block title or, where the
    FAA printed it first or omitted the title, an amendment line. A title or
    amendment with no heading above it (a DVA's airport continuing into its
    TAKEOFF MINIMUMS title) belongs to the entry already open.
    """
    entries: list[_Entry] = []
    city = ""
    for index, line in enumerate(lines):
        if not _opens_block(line.text):
            continue
        if (heading := _heading_before(lines, index)) is None:
            continue
        city = heading.city or city
        if entries:
            entries[-1].end = heading.start
        entries.append(_Entry(heading.lid, city, heading.name, index, len(lines)))
    return entries


def _heading_before(lines: list[_Line], index: int) -> _Heading | None:
    """The airport heading just above a title: city then airport, or the reverse.

    The city line is absent when the airport shares the previous one's city.
    """
    above = [lines[i].text if i >= 0 else "" for i in (index - 1, index - 2)]
    if airport := _airport_line(above[0]):
        city = _CITY_LINE.fullmatch(above[1])
        start = index - 2 if city else index - 1
        return _Heading(start, city and city["city"], airport["name"], airport["lid"])
    city = _CITY_LINE.fullmatch(above[0])
    if city and (airport := _airport_line(above[1])):
        return _Heading(index - 2, city["city"], airport["name"], airport["lid"])
    return None


def _block(entry: _Entry, lines: list[_Line], volume: str) -> AirportBlock:
    body = lines[entry.first : entry.end]
    return AirportBlock(
        lid=entry.lid,
        city=entry.city,
        name=entry.name,
        text="\n".join(join_lines(line.text for line in body)),
        volume=volume,
        pages=tuple(sorted({line.page for line in body})),
    )


def _mentioned_only(lines: list[_Line]) -> set[str]:
    """Parenthesized identifiers that never end a possible heading line.

    pdfinfo names a destination for navaids, airways and procedure
    originators that appear in parentheses in running text, e.g.
    ``Linden (LIN) VOR/DME`` or ``(USA)`` in an amendment line. Any
    non-amendment line ending in ``(ID)`` counts as a possible heading, so a
    heading too unusual to parse still leaves its destination unmatched.
    """
    heading_ids = {
        candidate["lid"]
        for line in lines
        if not is_amendment(line.text)
        and (candidate := _HEADING_CANDIDATE.fullmatch(line.text))
    }
    mentioned = {
        ident for line in lines for ident in _PARENTHESIZED_ID.findall(line.text)
    }
    return mentioned - heading_ids


def _warnings(
    blocks: list[AirportBlock], destinations: set[str], volume: str
) -> list[str]:
    lids = [block.lid for block in blocks]
    return [
        *(
            f"{volume}: destination ({name}) has no block"
            for name in sorted(destinations - set(lids))
        ),
        *(
            f"{volume}: block {lid} has no named destination"
            for lid in lids
            if len(lid) == _DESTINATION_ID_LENGTH and lid not in destinations
        ),
        *(
            f"{volume}: duplicate LID {lid}"
            for lid, count in collections.Counter(lids).items()
            if count > 1
        ),
    ]
