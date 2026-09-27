"""Tests for odp_kml.kml, the hand-written KML 2.2 writer."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from odp_kml.geo import LatLon
from odp_kml.kml import render_kml, write_kml
from odp_kml.shapes import AirportDrawing, Label, Polyline, Style

ALLOWED_ELEMENTS = {
    "kml",
    "Document",
    "Folder",
    "name",
    "Style",
    "styleUrl",
    "LineStyle",
    "color",
    "width",
    "IconStyle",
    "scale",
    "Placemark",
    "LineString",
    "coordinates",
    "Point",
}

GOLDEN_KML = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>Doc</name>
    <Style id="route-0">
      <LineStyle>
        <color>ff8b3a1e</color>
        <width>6</width>
      </LineStyle>
    </Style>
    <Style id="radial-0">
      <LineStyle>
        <color>ff8b3a1e</color>
        <width>3</width>
      </LineStyle>
    </Style>
    <Style id="hold-0">
      <LineStyle>
        <color>ff8b3a1e</color>
        <width>5</width>
      </LineStyle>
    </Style>
    <Style id="vcoa-0">
      <LineStyle>
        <color>ff8b3a1e</color>
        <width>3</width>
      </LineStyle>
    </Style>
    <Style id="route-1">
      <LineStyle>
        <color>ff1e1ec8</color>
        <width>6</width>
      </LineStyle>
    </Style>
    <Style id="radial-1">
      <LineStyle>
        <color>ff1e1ec8</color>
        <width>3</width>
      </LineStyle>
    </Style>
    <Style id="hold-1">
      <LineStyle>
        <color>ff1e1ec8</color>
        <width>5</width>
      </LineStyle>
    </Style>
    <Style id="vcoa-1">
      <LineStyle>
        <color>ff1e1ec8</color>
        <width>3</width>
      </LineStyle>
    </Style>
    <Style id="route-2">
      <LineStyle>
        <color>ff287814</color>
        <width>6</width>
      </LineStyle>
    </Style>
    <Style id="radial-2">
      <LineStyle>
        <color>ff287814</color>
        <width>3</width>
      </LineStyle>
    </Style>
    <Style id="hold-2">
      <LineStyle>
        <color>ff287814</color>
        <width>5</width>
      </LineStyle>
    </Style>
    <Style id="vcoa-2">
      <LineStyle>
        <color>ff287814</color>
        <width>3</width>
      </LineStyle>
    </Style>
    <Style id="route-3">
      <LineStyle>
        <color>ffa01e78</color>
        <width>6</width>
      </LineStyle>
    </Style>
    <Style id="radial-3">
      <LineStyle>
        <color>ffa01e78</color>
        <width>3</width>
      </LineStyle>
    </Style>
    <Style id="hold-3">
      <LineStyle>
        <color>ffa01e78</color>
        <width>5</width>
      </LineStyle>
    </Style>
    <Style id="vcoa-3">
      <LineStyle>
        <color>ffa01e78</color>
        <width>3</width>
      </LineStyle>
    </Style>
    <Style id="route-4">
      <LineStyle>
        <color>ff0078e6</color>
        <width>6</width>
      </LineStyle>
    </Style>
    <Style id="radial-4">
      <LineStyle>
        <color>ff0078e6</color>
        <width>3</width>
      </LineStyle>
    </Style>
    <Style id="hold-4">
      <LineStyle>
        <color>ff0078e6</color>
        <width>5</width>
      </LineStyle>
    </Style>
    <Style id="vcoa-4">
      <LineStyle>
        <color>ff0078e6</color>
        <width>3</width>
      </LineStyle>
    </Style>
    <Style id="route-5">
      <LineStyle>
        <color>ff14466e</color>
        <width>6</width>
      </LineStyle>
    </Style>
    <Style id="radial-5">
      <LineStyle>
        <color>ff14466e</color>
        <width>3</width>
      </LineStyle>
    </Style>
    <Style id="hold-5">
      <LineStyle>
        <color>ff14466e</color>
        <width>5</width>
      </LineStyle>
    </Style>
    <Style id="vcoa-5">
      <LineStyle>
        <color>ff14466e</color>
        <width>3</width>
      </LineStyle>
    </Style>
    <Style id="label">
      <IconStyle>
        <scale>0</scale>
      </IconStyle>
    </Style>
    <Folder>
      <name>KABC – Test Airport</name>
      <Placemark>
        <name>V123</name>
        <styleUrl>#route-0</styleUrl>
        <LineString>
          <coordinates>2.00000,1.00000 4.00000,3.00000</coordinates>
        </LineString>
      </Placemark>
      <Placemark>
        <name>RWY1</name>
        <styleUrl>#label</styleUrl>
        <Point>
          <coordinates>6.00000,5.00000</coordinates>
        </Point>
      </Placemark>
    </Folder>
  </Document>
</kml>
"""


def _tiny_drawing() -> AirportDrawing:
    return AirportDrawing(
        lid="KABC",
        name="Test Airport",
        shapes=(
            Polyline(
                name="V123",
                style=Style.ROUTE,
                points=(LatLon(1, 2), LatLon(3, 4)),
            ),
            Label(text="RWY1", at=LatLon(5, 6)),
        ),
    )


def test_render_kml_matches_golden_string():
    assert render_kml([_tiny_drawing()], document_name="Doc") == GOLDEN_KML


def test_render_kml_uses_only_allowed_elements_and_valid_style_refs():
    drawing = AirportDrawing(
        lid="KXYZ",
        name="Other Airport",
        shapes=(
            Polyline(
                name="R1", style=Style.RADIAL, points=(LatLon(0, 0), LatLon(1, 1))
            ),
            Polyline(name="H1", style=Style.HOLD, points=(LatLon(0, 0), LatLon(1, 1))),
            Polyline(name="C1", style=Style.VCOA, points=(LatLon(0, 0), LatLon(1, 1))),
            Label(text="L1", at=LatLon(2, 2)),
        ),
    )
    xml_text = render_kml([drawing], document_name="Doc")
    root = ET.fromstring(xml_text)

    tags = {element.tag.rsplit("}", 1)[-1] for element in root.iter()}
    assert tags <= ALLOWED_ELEMENTS

    defined_style_ids = {
        style.attrib["id"]
        for style in root.iter("{http://www.opengis.net/kml/2.2}Style")
    }
    style_urls = {
        style_url.text.removeprefix("#")
        for style_url in root.iter("{http://www.opengis.net/kml/2.2}styleUrl")
    }
    assert style_urls <= defined_style_ids


def test_render_kml_sorts_folders_by_lid():
    drawing_b = AirportDrawing(lid="KBBB", name="Bravo", shapes=())
    drawing_a = AirportDrawing(lid="KAAA", name="Alpha", shapes=())
    xml_text = render_kml([drawing_b, drawing_a], document_name="Doc")
    root = ET.fromstring(xml_text)

    folder_names = [
        folder.find("{http://www.opengis.net/kml/2.2}name").text
        for folder in root.iter("{http://www.opengis.net/kml/2.2}Folder")
    ]
    assert folder_names == ["KAAA – Alpha", "KBBB – Bravo"]


def test_render_kml_escapes_special_characters_in_names():
    drawing = AirportDrawing(
        lid="KKKK",
        name='Tom & Jerry\'s "Airport"',
        shapes=(Label(text="A <B> & C", at=LatLon(0, 0)),),
    )
    xml_text = render_kml([drawing], document_name="Doc")

    assert "&amp;" in xml_text
    assert "A &lt;B&gt; &amp; C" in xml_text
    # Round-trips back to the original text once parsed.
    root = ET.fromstring(xml_text)
    folder_name = root.find(
        ".//{http://www.opengis.net/kml/2.2}Folder/{http://www.opengis.net/kml/2.2}name"
    )
    assert folder_name.text == 'KKKK – Tom & Jerry\'s "Airport"'


def test_write_kml_writes_utf8_with_xml_declaration(tmp_path):
    out = tmp_path / "out.kml"
    write_kml([_tiny_drawing()], out, document_name="Doc")

    raw = out.read_bytes()
    assert raw.decode("utf-8") == GOLDEN_KML
    assert raw.startswith(b'<?xml version="1.0" encoding="UTF-8"?>')
