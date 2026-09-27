"""Tests for odp_kml.testgrid: the synthetic construction grid."""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET

from odp_kml.geo import LatLon
from odp_kml.kml import render_kml
from odp_kml.shapes import Label
from odp_kml.testgrid import cells, draw_grid
from tests.test_kml import ALLOWED_ELEMENTS

ORIGIN = LatLon(38.5, -117.5)
SPACING_DEG = 0.5
COLUMNS = 6


def test_cells_are_unique_and_laid_out_row_major():
    grid = cells(ORIGIN, SPACING_DEG, COLUMNS)

    assert len(grid) == 28
    assert len({cell.code for cell in grid}) == len(grid)
    assert len({cell.title for cell in grid}) == len(grid)
    assert [cell.code for cell in grid[:8]] == [
        "A1",
        "A2",
        "A3",
        "A4",
        "A5",
        "A6",
        "B1",
        "B2",
    ]


def test_draw_grid_labels_every_cell_and_uses_only_allowed_kml_elements():
    grid = cells(ORIGIN, SPACING_DEG, COLUMNS)
    drawings = draw_grid(ORIGIN, SPACING_DEG, COLUMNS)

    assert len(drawings) == len(grid)
    for cell, drawing in zip(grid, drawings, strict=True):
        titles = [s.text for s in drawing.shapes if isinstance(s, Label)]
        assert f"{cell.code}: {cell.title}" in titles

    xml_text = render_kml(drawings, document_name="Test Grid")
    root = ET.fromstring(xml_text)
    tags = {element.tag.rsplit("}", 1)[-1] for element in root.iter()}
    assert tags <= ALLOWED_ELEMENTS


# Recorded from a known-good render of `draw_grid()`. Update deliberately
# (and explain why in the commit) whenever a geometry change legitimately
# alters the grid's output.
GOLDEN_SHA256 = "ed5ed4d91626115fafab924f5fd03d05414f2b578120fdf28acc2427d4ebc546"


def test_render_kml_of_grid_matches_recorded_hash():
    xml_text = render_kml(draw_grid(), document_name="Test Grid")

    assert hashlib.sha256(xml_text.encode()).hexdigest() == GOLDEN_SHA256
