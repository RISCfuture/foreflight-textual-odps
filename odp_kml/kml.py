"""Hand-written KML 2.2 writer, limited to the subset ForeFlight renders.

ForeFlight silently ignores KML elements outside a small subset, so this
writer emits only that subset: Document, Folder, Style/LineStyle/IconStyle,
Placemark, LineString, and Point. Anything else (description, ExtendedData,
LabelStyle, altitudeMode, tessellate, extrude, Icon, BalloonStyle, gx:*) is
never written. ForeFlight draws a point's label in its icon's color, so a
label takes its airport's color from IconStyle, with the icon scaled down
until ForeFlight stops drawing it.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from pathlib import Path

from .geo import LatLon
from .palette import PALETTE_SIZE
from .shades import shades
from .shapes import AirportDrawing, Label, Polyline, Style

KML_NAMESPACE = "http://www.opengis.net/kml/2.2"
LABEL_STYLE_ID = "label"
# ForeFlight draws a default-size icon at scale 0 but none at all at 0.1.
LABEL_ICON_SCALE = "0.1"

# Line width per shape style.
_LINE_WIDTHS: dict[Style, int] = {
    Style.ROUTE: 10,
    Style.RADIAL: 5,
    Style.HOLD: 8,
    Style.VCOA: 5,
}

# One color per palette index, in KML "aabbggrr" order: blue, red, green,
# purple, orange, brown. Chosen to stay legible over sectional tints and to
# avoid ForeFlight's magenta active-leg color.
PALETTE_COLORS: tuple[str, ...] = (
    "ff8b3a1e",
    "ff1e1ec8",
    "ff287814",
    "ffa01e78",
    "ff0078e6",
    "ff14466e",
)
assert len(PALETTE_COLORS) == PALETTE_SIZE


def render_kml(drawings: Iterable[AirportDrawing], *, document_name: str) -> str:
    """Render `drawings` as a KML 2.2 document string.

    Airports become one Folder each, sorted by LID; within a folder,
    placemarks appear in the order given.
    """
    kml = ET.Element("kml", {"xmlns": KML_NAMESPACE})
    document = ET.SubElement(kml, "Document")
    _add_text_child(document, "name", document_name)
    for style in _build_shared_styles():
        document.append(style)
    shade_styles: dict[str, ET.Element] = {}
    folders = [
        _build_folder(drawing, shade_styles)
        for drawing in sorted(drawings, key=lambda drawing: drawing.lid)
    ]
    document.extend(shade_styles.values())
    document.extend(folders)

    ET.indent(kml, space="  ")
    body = ET.tostring(kml, encoding="unicode")
    return f'<?xml version="1.0" encoding="UTF-8"?>\n{body}\n'


def write_kml(
    drawings: Iterable[AirportDrawing], out: Path, *, document_name: str
) -> None:
    """Write the rendered KML document to `out` as UTF-8, with an XML declaration."""
    out.write_text(render_kml(drawings, document_name=document_name), encoding="utf-8")


def _build_shared_styles() -> list[ET.Element]:
    line_styles = [
        _build_line_style(style, palette)
        for palette in range(PALETTE_SIZE)
        for style in Style
    ]
    label_styles = [_build_label_style(palette) for palette in range(PALETTE_SIZE)]
    return [*line_styles, *label_styles]


def line_style_id(style: Style, palette: int) -> str:
    """The shared style id for a shape style drawn in a palette color."""
    return f"{style.value}-{palette}"


def _build_line_style(
    style: Style, palette: int, style_id: str | None = None, color: str | None = None
) -> ET.Element:
    element = ET.Element("Style", {"id": style_id or line_style_id(style, palette)})
    width = _LINE_WIDTHS[style]
    color = color or PALETTE_COLORS[palette]
    line_style = ET.SubElement(element, "LineStyle")
    _add_text_child(line_style, "color", color)
    _add_text_child(line_style, "width", str(width))
    return element


def label_style_id(palette: int) -> str:
    """The shared style id for labels in a palette color."""
    return f"{LABEL_STYLE_ID}-{palette}"


def _build_label_style(palette: int) -> ET.Element:
    element = ET.Element("Style", {"id": label_style_id(palette)})
    icon_style = ET.SubElement(element, "IconStyle")
    _add_text_child(icon_style, "color", PALETTE_COLORS[palette])
    _add_text_child(icon_style, "scale", LABEL_ICON_SCALE)
    return element


def _build_folder(
    drawing: AirportDrawing, shade_styles: dict[str, ET.Element]
) -> ET.Element:
    """The airport's placemarks. Each runway group's lines take their own
    shade of the airport's color (added to `shade_styles` as needed); lines
    the routes share keep the color itself."""
    folder = ET.Element("Folder")
    _add_text_child(folder, "name", f"{drawing.lid} – {drawing.name}")
    style_of = _group_styles(drawing, shade_styles)
    for shape in drawing.shapes:
        if isinstance(shape, Polyline):
            folder.append(_build_polyline_placemark(shape, style_of(shape)))
        else:
            folder.append(_build_label_placemark(shape, drawing.palette))
    return folder


def _group_styles(drawing: AirportDrawing, shade_styles: dict[str, ET.Element]):
    """A function giving each polyline's style id at this airport."""
    groups = sorted(
        {shape.group for shape in drawing.shapes if isinstance(shape, Polyline)} - {()},
        key=_runway_order,
    )
    base = PALETTE_COLORS[drawing.palette]
    others = [color for color in PALETTE_COLORS if color != base]
    colors = shades(base, len(groups), others)
    index = {group: i for i, group in enumerate(groups)} if len(groups) > 1 else {}

    def style_of(line: Polyline) -> str:
        if line.group not in index:
            return line_style_id(line.style, drawing.palette)
        i = index[line.group]
        style_id = f"{line_style_id(line.style, drawing.palette)}-{len(groups)}-{i}"
        if style_id not in shade_styles:
            shade_styles[style_id] = _build_line_style(
                line.style, drawing.palette, style_id, colors[i]
            )
        return style_id

    return style_of


def _runway_order(group: tuple[str, ...]) -> list[tuple[int, str]]:
    """Runway groups in numeric order: 4, 16L/16R, 22, 34."""
    return [(int(re.match(r"\d*", runway)[0] or 0), runway) for runway in group]


def _build_polyline_placemark(polyline: Polyline, style_id: str) -> ET.Element:
    placemark = ET.Element("Placemark")
    _add_text_child(placemark, "name", polyline.name)
    _add_text_child(placemark, "styleUrl", f"#{style_id}")
    line_string = ET.SubElement(placemark, "LineString")
    _add_text_child(line_string, "coordinates", _format_coordinates(polyline.points))
    return placemark


def _build_label_placemark(label: Label, palette: int) -> ET.Element:
    placemark = ET.Element("Placemark")
    _add_text_child(placemark, "name", label.text)
    _add_text_child(placemark, "styleUrl", f"#{label_style_id(palette)}")
    point = ET.SubElement(placemark, "Point")
    _add_text_child(point, "coordinates", _format_coordinate(label.at))
    return placemark


def _format_coordinate(point: LatLon) -> str:
    return f"{point.lon:.5f},{point.lat:.5f}"


def _format_coordinates(points: Iterable[LatLon]) -> str:
    return " ".join(_format_coordinate(point) for point in points)


def _add_text_child(parent: ET.Element, tag: str, text: str) -> ET.Element:
    child = ET.SubElement(parent, tag)
    child.text = text
    return child
