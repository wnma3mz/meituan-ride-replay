"""Tests for the shared helpers, centred on the bugs that motivated them."""
from __future__ import annotations

import csv

import pytest

from ridedata import common


class TestDurationText:
    """The old formatter dropped the hour component entirely."""

    def test_keeps_hours(self):
        # The regression: 23810s used to render as "396分50秒".
        assert common.duration_text(23810) == "6小时36分50秒"

    def test_real_stored_values(self):
        # Values taken from data/three-years/days/*.json, which stored the
        # minutes-only form for all 21 rides over an hour.
        assert common.duration_text(7178) == "1小时59分38秒"
        assert common.duration_text(6969) == "1小时56分09秒"

    def test_under_an_hour_has_no_hour_part(self):
        assert common.duration_text(3599) == "59分59秒"
        assert common.duration_text(90) == "1分30秒"

    def test_exact_hour(self):
        assert common.duration_text(3600) == "1小时00分00秒"

    def test_zero_and_rounding(self):
        assert common.duration_text(0) == "0分00秒"
        assert common.duration_text(59.6) == "1分00秒"

    @pytest.mark.parametrize("seconds", [0, 59, 60, 3599, 3600, 7178, 23810, 86399])
    def test_never_loses_time(self, seconds):
        """Whatever the format, the parts must add back up to the input."""
        text = common.duration_text(seconds)
        hours = 0
        rest = text
        if "小时" in text:
            head, rest = text.split("小时", 1)
            hours = int(head)
        minutes = int(rest.split("分")[0])
        secs = int(rest.split("分")[1].rstrip("秒"))
        assert hours * 3600 + minutes * 60 + secs == round(seconds)


class TestParseLonlat:
    def test_basic(self):
        assert common.parse_lonlat("116.4,39.9") == (116.4, 39.9)

    def test_tolerates_extra_fields(self):
        # The two old copies disagreed on this input.
        assert common.parse_lonlat("116.4,39.9,0") == (116.4, 39.9)

    @pytest.mark.parametrize("bad", [None, "", "116.4", "abc,def", ","])
    def test_rejects_unusable(self, bad):
        assert common.parse_lonlat(bad) is None


class TestHaversine:
    def test_known_distance(self):
        # Beijing city centre to roughly 3.4 km north-east.
        metres = common.haversine((116.40, 39.90), (116.43, 39.92))
        assert 3300 < metres < 3500

    def test_zero_for_same_point(self):
        assert common.haversine((116.4, 39.9), (116.4, 39.9)) == pytest.approx(0)

    def test_string_wrapper_matches(self):
        pair = ("116.40,39.90", "116.43,39.92")
        assert common.haversine_lonlat(*pair) == pytest.approx(
            common.haversine((116.40, 39.90), (116.43, 39.92))
        )

    def test_string_wrapper_rejects_missing(self):
        assert common.haversine_lonlat(None, "116.4,39.9") is None


class TestTrackPoints:
    def test_counts_hash_separated(self):
        assert common.track_point_count("1,2#3,4#5,6") == 3

    def test_empty(self):
        assert common.track_point_count(None) == 0
        assert common.track_point_count("") == 0

    def test_full_track_needs_more_than_two(self):
        assert not common.has_full_track(2)
        assert common.has_full_track(3)
        assert not common.has_full_track(0)
        assert not common.has_full_track(None)


class TestIO:
    def test_json_round_trip_keeps_chinese_readable(self, tmp_path):
        target = tmp_path / "nested" / "out.json"
        common.write_json(target, {"地点": "望京东园"})
        assert "望京东园" in target.read_text(encoding="utf-8")
        assert common.read_json(target) == {"地点": "望京东园"}

    def test_read_json_survives_corruption(self, tmp_path):
        broken = tmp_path / "broken.json"
        broken.write_text("{not json", encoding="utf-8")
        assert common.read_json(broken, default={}) == {}

    def test_read_json_survives_missing(self, tmp_path):
        assert common.read_json(tmp_path / "nope.json", default=[]) == []

    def test_csv_uses_bom_for_excel(self, tmp_path):
        target = tmp_path / "out.csv"
        common.write_csv(target, [{"a": 1, "b": "北京"}], ["a", "b"])
        assert target.read_bytes().startswith(b"\xef\xbb\xbf")

    def test_csv_selects_and_orders_fields(self, tmp_path):
        target = tmp_path / "out.csv"
        common.write_csv(
            target,
            [{"b": "2", "a": "1", "ignored": "x"}],
            ["a", "b"],
        )
        with target.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        assert rows == [{"a": "1", "b": "2"}]

    def test_csv_fills_missing_keys(self, tmp_path):
        target = tmp_path / "out.csv"
        common.write_csv(target, [{"a": "1"}], ["a", "b"])
        with target.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        assert rows == [{"a": "1", "b": ""}]


class TestBeijingTime:
    def test_offset(self):
        assert common.BEIJING.utcoffset(None).total_seconds() == 8 * 3600

    def test_formatting(self):
        # 2025-04-04 12:00:00 +08:00
        ms = 1743739200000
        assert common.beijing_date(ms) == "2025-04-04"
        assert common.beijing_time(ms) == "12:00:00"
