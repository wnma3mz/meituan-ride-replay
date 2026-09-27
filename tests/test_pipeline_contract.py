"""The contract between the two halves: what `ridedata` writes, `ridevideo` reads.

These tests build their own fixtures rather than reading the real data
directory, so they pass on a fresh clone with no exported data.
"""
from __future__ import annotations

import json

import pytest

from ridevideo.rides.load import GPS, INFERRED, load_day


@pytest.fixture
def day_json(tmp_path, monkeypatch):
    """A day export in the shape `ridedata.bulk` writes."""
    data = tmp_path / "data"
    (data / "three-years" / "days").mkdir(parents=True)
    document = {
        "date": "2025-04-04",
        "orders": [
            {
                "orderId": "1",
                "startTimestamp": 1_743_739_200_000,
                "endTimestamp": 1_743_742_800_000,
                "durationSeconds": 3600,
                "durationText": "60分00秒",
                "startLonlat": "116.40,39.90",
                "endLonlat": "116.45,39.95",
                "trackPointCount": 2,
                "trackPoints": "116.40,39.90#116.45,39.95",
            },
            {
                "orderId": "2",
                "startTimestamp": 1_743_746_400_000,
                "endTimestamp": 1_743_750_000_000,
                "durationSeconds": 3600,
                "durationText": "60分00秒",
                "startLonlat": "116.45,39.95",
                "endLonlat": "116.50,40.00",
                "trackPointCount": 4,
                "trackPoints": "116.45,39.95#116.46,39.96#116.48,39.98#116.50,40.00",
            },
        ],
    }
    (data / "three-years" / "days" / "2025-04-04.json").write_text(
        json.dumps(document, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("RIDE_DATA_DIR", str(data))
    # load.SRC is bound at import time, so point it at the fixture too.
    monkeypatch.setattr("ridevideo.rides.load.SRC", data)
    return data


class TestDayContract:
    def test_loads_both_rides(self, day_json):
        day = load_day("2025-04-04")
        assert day.date == "2025-04-04"
        assert len(day.rides) == 2

    def test_track_source_reflects_point_count(self, day_json):
        """Two points means the path was inferred; more means real GPS."""
        day = load_day("2025-04-04")
        assert day.rides[0].track_source == INFERRED
        assert day.rides[1].track_source == GPS

    def test_has_inferred_drives_the_on_screen_disclaimer(self, day_json):
        assert load_day("2025-04-04").has_inferred is True

    def test_duration_text_is_derived_not_trusted(self, day_json):
        """The stored text is the buggy minutes-only form; loading must fix it."""
        day = load_day("2025-04-04")
        assert day.rides[0].duration_text == "1小时00分00秒"

    def test_rides_sorted_by_start(self, day_json):
        day = load_day("2025-04-04")
        assert day.rides[0].start_ms < day.rides[1].start_ms

    def test_missing_date_raises(self, day_json):
        with pytest.raises((FileNotFoundError, ValueError)):
            load_day("1999-01-01")


class TestOneVideoPerDay:
    """A day is the unit of a video, and nothing in it gets dropped.

    The previous arrangement rendered only *connected groups*, which silently
    lost every day whose rides never connected — 20 of 49 real days, including
    one 92-minute ride.  These tests pin the day-level contract.
    """

    def _write_day(self, tmp_path, monkeypatch, orders):
        data = tmp_path / "data"
        (data / "three-years" / "days").mkdir(parents=True)
        (data / "three-years" / "days" / "2025-04-04.json").write_text(
            json.dumps({"date": "2025-04-04", "orders": orders}, ensure_ascii=False),
            encoding="utf-8")
        monkeypatch.setenv("RIDE_DATA_DIR", str(data))
        monkeypatch.setattr("ridevideo.rides.load.SRC", data)
        return data

    def _order(self, start_h, end_h, lonlat_a, lonlat_b, points=2):
        base = 1_743_739_200_000  # 2025-04-04 12:00 +08:00
        hour = 3_600_000
        track = "#".join([lonlat_a] * max(points - 1, 1) + [lonlat_b])
        return {
            "orderId": f"{start_h}",
            "startTimestamp": base + start_h * hour,
            "endTimestamp": base + end_h * hour,
            "durationSeconds": (end_h - start_h) * 3600,
            "startLonlat": lonlat_a,
            "endLonlat": lonlat_b,
            "trackPointCount": points,
            "trackPoints": track,
        }

    def test_single_lone_ride_still_makes_a_day(self, tmp_path, monkeypatch):
        """One long ride that can never form a group must still load."""
        self._write_day(tmp_path, monkeypatch,
                        [self._order(0, 2, "116.40,39.90", "116.50,40.00")])
        day = load_day("2025-04-04")
        assert len(day.rides) == 1

    def test_disconnected_rides_stay_in_one_day(self, tmp_path, monkeypatch):
        """Two rides hours apart belong to the same video, marked as a transfer."""
        self._write_day(tmp_path, monkeypatch, [
            self._order(0, 1, "116.40,39.90", "116.45,39.95"),
            self._order(8, 9, "116.80,40.30", "116.85,40.35"),
        ])
        day = load_day("2025-04-04")
        assert len(day.rides) == 2

    def test_transfer_is_detected_between_distant_rides(self, tmp_path, monkeypatch):
        from ridevideo.rides.camera import TRANSFER_KM, build_timeline

        self._write_day(tmp_path, monkeypatch, [
            self._order(0, 1, "116.40,39.90", "116.45,39.95"),
            self._order(8, 9, "116.80,40.30", "116.85,40.35"),
        ])
        segments = build_timeline(load_day("2025-04-04"), ride_seconds=20.0)
        transfers = [s for s in segments
                     if s.kind == "gap" and s.transfer_km >= TRANSFER_KM]
        assert transfers, "相距很远的两段之间应判为转场"

    def test_nearby_consecutive_rides_are_not_a_transfer(self, tmp_path, monkeypatch):
        from ridevideo.rides.camera import TRANSFER_KM, build_timeline

        self._write_day(tmp_path, monkeypatch, [
            self._order(0, 1, "116.40,39.90", "116.45,39.95"),
            self._order(1, 2, "116.4502,39.9502", "116.50,40.00"),
        ])
        segments = build_timeline(load_day("2025-04-04"), ride_seconds=20.0)
        transfers = [s for s in segments
                     if s.kind == "gap" and s.transfer_km >= TRANSFER_KM]
        assert not transfers, "接得上的两段不该判为转场"

    def test_batch_discovers_every_day_with_orders(self, tmp_path):
        from ridevideo.rides.batch import discover_days

        days = tmp_path / "days"
        days.mkdir()
        for date, orders in (("2025-04-04", [{"orderId": "1"}]),
                             ("2025-04-05", [{"orderId": "2"}]),
                             ("2025-04-06", [])):
            (days / f"{date}.json").write_text(
                json.dumps({"date": date, "orders": orders}), encoding="utf-8")
        jobs = discover_days(days)
        assert [j.key for j in jobs] == ["2025-04-04", "2025-04-05"]
        assert all(job.kind == "day" for job in jobs)

    def test_batch_skips_corrupt_files(self, tmp_path):
        from ridevideo.rides.batch import discover_days

        days = tmp_path / "days"
        days.mkdir()
        (days / "bad.json").write_text("{broken", encoding="utf-8")
        (days / "2025-04-04.json").write_text(
            json.dumps({"date": "2025-04-04", "orders": [{"orderId": "1"}]}),
            encoding="utf-8")
        assert [j.key for j in discover_days(days)] == ["2025-04-04"]


class TestPackageIndependence:
    def test_ridevideo_does_not_import_ridedata(self):
        """The rendering package must stand alone: it reads files, not code.

        Checks the AST rather than the raw text, so a docstring mentioning the
        other package by name does not count as a dependency.
        """
        import ast
        import pathlib

        import ridevideo
        root = pathlib.Path(ridevideo.__file__).parent
        offenders = []
        for source in root.rglob("*.py"):
            tree = ast.parse(source.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                if any(name.split(".")[0] == "ridedata" for name in names):
                    offenders.append(f"{source.name}:{node.lineno}")
        assert not offenders, f"ridevideo imports ridedata at {offenders}"
