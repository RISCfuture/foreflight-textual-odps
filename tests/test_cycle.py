"""Cycle arithmetic: 28-day AIRAC cycles from the 2020-12-03 datum."""

import datetime as dt

import pytest

from odp_kml.cycle import Cycle


class TestCycleContaining:
    def test_effective_date_is_a_cycle_boundary_on_or_before_today(self):
        cycle = Cycle.containing(dt.date(2026, 9, 23))
        assert cycle.effective == dt.date(2026, 9, 3)

    def test_day_of_boundary_belongs_to_the_new_cycle(self):
        assert Cycle.containing(dt.date(2026, 10, 1)).effective == dt.date(2026, 10, 1)

    def test_day_before_boundary_belongs_to_the_old_cycle(self):
        assert Cycle.containing(dt.date(2026, 9, 30)).effective == dt.date(2026, 9, 3)


class TestCycleNavigation:
    def test_next_is_28_days_later(self):
        assert Cycle(dt.date(2026, 9, 3)).next.effective == dt.date(2026, 10, 1)

    def test_constructing_off_boundary_is_rejected(self):
        with pytest.raises(ValueError):
            Cycle(dt.date(2026, 9, 4))


class TestCycleCodes:
    @pytest.mark.parametrize(
        ("effective", "code"),
        [
            (dt.date(2026, 1, 22), "2601"),
            (dt.date(2026, 9, 3), "2609"),
            (dt.date(2026, 12, 24), "2613"),
            (dt.date(2027, 1, 21), "2701"),
            (dt.date(2025, 12, 25), "2513"),
        ],
    )
    def test_dtpp_code_counts_cycles_within_the_calendar_year(self, effective, code):
        assert Cycle(effective).dtpp_code == code

    def test_nasr_tag_uses_day_month_year(self):
        assert Cycle(dt.date(2026, 9, 3)).nasr_tag == "03_Sep_2026"

    def test_iso_string(self):
        assert Cycle(dt.date(2026, 9, 3)).iso == "2026-09-03"
        assert Cycle.from_iso("2026-09-03").effective == dt.date(2026, 9, 3)
