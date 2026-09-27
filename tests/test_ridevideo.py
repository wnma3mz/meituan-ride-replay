"""Tests for the rendering side: formatting, paths, caches, timeline."""
from __future__ import annotations

import json

import pytest

from ridevideo import format as vfmt
from ridevideo import paths
from ridevideo.swiftkit import JsonCache


class TestFormat:
    def test_duration_keeps_hours(self):
        assert vfmt.duration_text(23810) == "6小时36分50秒"

    def test_matches_the_data_side(self):
        """Both packages format durations identically.

        They deliberately hold separate copies so neither imports the other, so
        a test has to hold them together.
        """
        from ridedata.common import duration_text as data_side

        for seconds in (0, 59, 90, 3599, 3600, 7178, 23810):
            assert vfmt.duration_text(seconds) == data_side(seconds)

    def test_short_form_drops_seconds(self):
        assert vfmt.duration_short(23810) == "6 小时 36 分"
        assert vfmt.duration_short(3540) == "59 分"

    def test_clock(self):
        assert vfmt.clock(1743739200000) == "12:00"


class TestPaths:
    def test_swift_sources_exist(self):
        for name in ("snap", "route", "geo"):
            assert (paths.SWIFT_DIR / f"{name}.swift").exists(), name

    def test_data_root_override(self, monkeypatch, tmp_path):
        monkeypatch.setenv("RIDE_DATA_DIR", str(tmp_path))
        assert paths.data_root() == tmp_path.resolve()

    def test_data_root_default_is_inside_project(self, monkeypatch):
        monkeypatch.delenv("RIDE_DATA_DIR", raising=False)
        assert paths.data_root() == paths.PROJECT_ROOT / "data"

    def test_derived_paths_follow_the_override(self, monkeypatch, tmp_path):
        monkeypatch.setenv("RIDE_DATA_DIR", str(tmp_path))
        assert paths.days_root() == tmp_path.resolve() / "three-years" / "days"
        assert paths.video_out() == tmp_path.resolve() / "video-out"


class TestJsonCache:
    def test_round_trip(self, tmp_path):
        target = tmp_path / "c.json"
        cache = JsonCache(target)
        cache.set("k", {"points": [[1, 2]]})
        cache.flush()
        assert json.loads(target.read_text(encoding="utf-8"))["k"]["points"] == [[1, 2]]

    def test_no_write_when_unchanged(self, tmp_path):
        target = tmp_path / "c.json"
        JsonCache(target).flush()
        assert not target.exists()

    def test_survives_corruption(self, tmp_path):
        target = tmp_path / "c.json"
        target.write_text("{broken", encoding="utf-8")
        assert JsonCache(target).get("anything") is None

    def test_survives_wrong_toplevel_type(self, tmp_path):
        target = tmp_path / "c.json"
        target.write_text("[1,2,3]", encoding="utf-8")
        assert JsonCache(target).data == {}

    def test_creates_parent_directory(self, tmp_path):
        cache = JsonCache(tmp_path / "deep" / "c.json")
        cache.set("k", 1)
        cache.flush()
        assert (tmp_path / "deep" / "c.json").exists()


class TestCoordParsing:
    """snapbin's stdout can carry framework log lines among the coordinates."""

    def test_plain_coordinates(self):
        from ridevideo.rides.basemap import _parse_coords

        assert _parse_coords("1.0,2.0\n3.5,4.5\n") == [(1.0, 2.0), (3.5, 4.5)]

    def test_skips_framework_noise(self):
        from ridevideo.rides.basemap import _parse_coords

        # This exact line came out of a real run and used to crash the render.
        noisy = "List has 3 nodes:\n1.0,2.0\nsome other log\n3.0,4.0\n"
        assert _parse_coords(noisy) == [(1.0, 2.0), (3.0, 4.0)]

    def test_skips_wrong_arity(self):
        from ridevideo.rides.basemap import _parse_coords

        assert _parse_coords("1.0,2.0,3.0\n4.0,5.0\n") == [(4.0, 5.0)]

    def test_empty(self):
        from ridevideo.rides.basemap import _parse_coords

        assert _parse_coords("") == []


class TestRouteCacheKey:
    def test_stable_and_directional(self):
        from ridevideo.rides.route import cache_key

        a, b = (116.4, 39.9), (116.5, 40.0)
        assert cache_key(a, b) == "116.4,39.9->116.5,40.0"
        assert cache_key(a, b) != cache_key(b, a)


class TestTimeline:
    """Pacing rules that keep the on-screen clock honest."""

    def _day(self, spans_minutes):
        from ridevideo.rides.load import Day, Ride

        rides = []
        cursor = 1_743_739_200_000
        for i, minutes in enumerate(spans_minutes):
            end = cursor + int(minutes * 60_000)
            rides.append(Ride(
                index=i,
                start=(116.4 + i * 0.01, 39.9),
                end=(116.4 + i * 0.01 + 0.005, 39.905),
                path=[(116.4 + i * 0.01, 39.9), (116.4 + i * 0.01 + 0.005, 39.905)],
                track_source="inferred",
                start_ms=cursor,
                end_ms=end,
                duration_text="",
            ))
            cursor = end + 5 * 60_000
        return Day(date="2025-04-04", rides=rides,
                   gap_minutes=[5.0] * (len(rides) - 1), title="")

    def test_short_leg_still_gets_screen_time(self):
        """A 7-minute leg beside an 86-minute one must stay readable."""
        from ridevideo.rides.camera import MIN_RIDE_SECONDS, build_timeline

        segments = build_timeline(self._day([86, 7]), ride_seconds=20.0)
        rides = [s for s in segments if s.kind == "ride"]
        assert len(rides) == 2
        assert min(s.video_duration for s in rides) >= MIN_RIDE_SECONDS - 1e-6

    def test_segments_are_contiguous(self):
        from ridevideo.rides.camera import build_timeline

        segments = build_timeline(self._day([30, 20, 10]), ride_seconds=24.0)
        for a, b in zip(segments, segments[1:]):
            assert a.video_end == pytest.approx(b.video_start)

    def test_longer_ride_gets_more_time(self):
        from ridevideo.rides.camera import build_timeline

        segments = build_timeline(self._day([60, 10]), ride_seconds=30.0)
        rides = [s for s in segments if s.kind == "ride"]
        assert rides[0].video_duration > rides[1].video_duration

    def test_suggested_length_scales_with_content(self):
        from ridevideo.rides.camera import suggest_ride_seconds

        short = suggest_ride_seconds(self._day([10, 10]))
        long = suggest_ride_seconds(self._day([60] * 12))
        assert short < long
        assert 8.0 <= short and long <= 46.0
