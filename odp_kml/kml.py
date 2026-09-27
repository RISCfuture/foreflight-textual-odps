"""Hand-written KML 2.2 writer, limited to the subset ForeFlight renders.

ForeFlight silently ignores KML elements outside a small subset, so this
writer emits only that subset: Document, Folder, Style/LineStyle/IconStyle,
Placemark, LineString, and Point. Anything else (description, ExtendedData,
LabelStyle, altitudeMode, tessellate, extrude, Icon, BalloonStyle, gx:*) is
never written.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Iterable
from pathlib import Path

from .geo import LatLon
from .shapes import AirportDrawing, Label, Polyline, Style

KML_NAMESPACE = "http://www.opengis.net/kml/2.2"
LABEL_STYLE_ID = "label"

# (style id, line width, color) for every shared LineStyle, in "aabbggrr" order.
_LINE_STYLES: tuple[tuple[Style, int, str], ...] = (
    (Style.ROUTE, 3, "ff8b3a1e"),
    (Style.RADIAL, 1, "ff8b3a1e"),
    (Style.HOLD, 2, "ff8b3a1e"),
    (Style.VCOA, 1, "ff8b3a1e"),
)


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
    for drawing in sorted(drawings, key=lambda drawing: drawing.lid):
        document.append(_build_folder(drawing))

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
        _build_line_style(style, width, color) for style, width, color in _LINE_STYLES
    ]
    return [*line_styles, _build_label_style()]


def _build_line_style(style: Style, width: int, color: str) -> ET.Element:
    element = ET.Element("Style", {"id": style.value})
    line_style = ET.SubElement(element, "LineStyle")
    _add_text_child(line_style, "color", color)
    _add_text_child(line_style, "width", str(width))
    return element


def _build_label_style() -> ET.Element:
    element = ET.Element("Style", {"id": LABEL_STYLE_ID})
    icon_style = ET.SubElement(element, "IconStyle")
    _add_text_child(icon_style, "scale", "0")
    return element


def _build_folder(drawing: AirportDrawing) -> ET.Element:
    folder = ET.Element("Folder")
    _add_text_child(folder, "name", f"{drawing.lid} – {drawing.name}")
    for shape in drawing.shapes:
        folder.append(_build_placemark(shape))
    return folder


def _build_placemark(shape: Polyline | Label) -> ET.Element:
    if isinstance(shape, Polyline):
        return _build_polyline_placemark(shape)
    return _build_label_placemark(shape)


def _build_polyline_placemark(polyline: Polyline) -> ET.Element:
    placemark = ET.Element("Placemark")
    _add_text_child(placemark, "name", polyline.name)
    _add_text_child(placemark, "styleUrl", f"#{polyline.style.value}")
    line_string = ET.SubElement(placemark, "LineString")
    _add_text_child(line_string, "coordinates", _format_coordinates(polyline.points))
    return placemark


def _build_label_placemark(label: Label) -> ET.Element:
    placemark = ET.Element("Placemark")
    _add_text_child(placemark, "name", label.text)
    _add_text_child(placemark, "styleUrl", f"#{LABEL_STYLE_ID}")
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
