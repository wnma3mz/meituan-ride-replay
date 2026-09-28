from __future__ import annotations

import json

from ridedata import bulk


def test_detail_cache_is_reused_by_order_id(tmp_path, monkeypatch):
    raw = tmp_path / "details"
    raw.mkdir()
    cached = {"code": 0, "data": {"bizData": {"mapInfo": {"startLonlat": "1,2"}}}}
    (raw / "0001-order-1.json").write_text(json.dumps(cached), encoding="utf-8")

    def fail(*args, **kwargs):
        raise AssertionError("cached detail should not call the API")

    monkeypatch.setattr(bulk.api, "fetch_order_detail", fail)
    rows = bulk.enrich_order_details(
        [{"orderId": "order-1", "startTimestamp": 0, "endTimestamp": 1000}],
        [], raw, retries=1, sleep_seconds=0,
    )
    assert rows[0]["detailStatus"] == "ok"
    assert rows[0]["detailRawFile"].endswith("0001-order-1.json")


def test_refresh_details_bypasses_cache(tmp_path, monkeypatch):
    raw = tmp_path / "details"
    raw.mkdir()
    (raw / "0001-order-1.json").write_text(json.dumps({"code": 0}), encoding="utf-8")
    calls = []

    def fetch(*args, **kwargs):
        calls.append(args[1])
        return {"code": 0, "data": {"bizData": {}}}, ""

    monkeypatch.setattr(bulk.api, "fetch_order_detail", fetch)
    bulk.enrich_order_details(
        [{"orderId": "order-1", "startTimestamp": 0, "endTimestamp": 1000}],
        [], raw, retries=1, sleep_seconds=0, refresh_details=True,
    )
    assert calls == ["order-1"]


def test_archive_previous_selection_uses_filter_name_and_preserves_files(tmp_path):
    (tmp_path / "days").mkdir()
    (tmp_path / "days" / "2025-01-01.json").write_text("{}", encoding="utf-8")
    (tmp_path / "details").mkdir()
    (tmp_path / "summary.json").write_text(json.dumps({"filter": "long-rides"}), encoding="utf-8")
    (tmp_path / "qualifying-days.csv").write_text("date\n", encoding="utf-8")

    archive = bulk.archive_previous_selection(tmp_path)

    assert archive == tmp_path / "archive" / "long-rides"
    assert (archive / "days" / "2025-01-01.json").exists()
    assert (tmp_path / "details").exists()
    assert not (tmp_path / "days").exists()
    assert not (tmp_path / "summary.json").exists()


def test_archive_keeps_detail_cache_for_reuse(tmp_path):
    (tmp_path / "days").mkdir()
    (tmp_path / "details").mkdir()
    (tmp_path / "summary.json").write_text(json.dumps({"filter": "old"}), encoding="utf-8")
    bulk.archive_previous_selection(tmp_path)
    assert (tmp_path / "details").exists()
    assert not (tmp_path / "archive" / "old" / "details").exists()

