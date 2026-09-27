#!/usr/bin/env python3
"""Export Meituan bike orders for one Beijing calendar date.

The only input is a HAR captured from a logged-in session: endpoints, request
bodies and paging live in `api.py`, and the HAR contributes only credentials.

    ride-data fetch 2025-04-04 --har capture.har

Details (start/end coordinates and track points) are fetched from the same
credentials, so one HAR is enough for the whole run.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

from . import api
from .common import BEIJING, duration_text, track_point_count, write_csv, write_json
from .paths import data_root

MAX_PAGES = 100

CSV_FIELDS = [
    "startTimeBeijing", "endTimeBeijing", "durationText", "durationSeconds",
    "originFeeYuan", "actualFeeYuan", "carbonEmissionsGram", "distanceMeter",
    "orderId", "bikeId", "bizId", "payStatus", "showRidingStatus", "discountDesc",
    "penaltyDesc", "evaluateStatus", "canEvaluate", "stationId", "stationName",
    "showSingleCarbon", "noticeBarList", "carbonRedirectUrl", "addInsureTag",
    "detailStatus", "startLonlat", "endLonlat", "rideMiles", "showTrack",
    "trackPointCount", "trackPoints",
]

DETAIL_FIELDS = ("startLonlat", "endLonlat", "rideMiles", "showTrack", "trackPoints")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("date", help="北京时间日期，如 2025-04-04")
    parser.add_argument(
        "--har", type=Path, required=True,
        help="从已登录会话导出的 HAR，只用于提取凭证",
    )
    parser.add_argument("--output-dir", type=Path, default=data_root())
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument(
        "--skip-details", action="store_true",
        help="只抓历史列表，不请求每笔订单详情（那样没有轨迹）",
    )
    parser.add_argument("--detail-retries", type=int, default=3)
    return parser.parse_args(argv)


def timestamp_ms(value: dt.datetime) -> int:
    return int(value.timestamp() * 1000)


def local_datetime(milliseconds: int | None) -> str | None:
    if milliseconds is None:
        return None
    return dt.datetime.fromtimestamp(milliseconds / 1000, BEIJING).isoformat()


def day_bounds(date_text: str) -> tuple[dt.datetime, dt.datetime]:
    day = dt.date.fromisoformat(date_text)
    start = dt.datetime.combine(day, dt.time.min, tzinfo=BEIJING)
    return start, start + dt.timedelta(days=1)


def fetch_history(headers: list[str], start: dt.datetime, end: dt.datetime,
                  page_size: int, raw_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Page backwards through the history list until it predates `start`."""
    max_timestamp = timestamp_ms(end) - 1
    start_timestamp = timestamp_ms(start)
    orders: dict[str, dict[str, Any]] = {}
    pages: list[dict[str, Any]] = []
    raw_dir.mkdir(parents=True, exist_ok=True)

    for page_number in range(1, MAX_PAGES + 1):
        response = api.fetch_history_page(headers, max_timestamp, page_size)
        page_data = response.get("data") or {}
        page_orders = page_data.get("ridingOrders") or []
        pages.append({
            "page": page_number,
            "requestMaxTimestamp": max_timestamp,
            "lastTimestamp": page_data.get("lastTimestamp"),
            "returnedOrders": len(page_orders),
        })
        write_json(raw_dir / f"page-{page_number:03d}.json", response)
        for order in page_orders:
            orders[str(order.get("orderId"))] = order

        stamps = [o.get("startTimestamp") for o in page_orders
                  if o.get("startTimestamp") is not None]
        oldest = min(stamps) if stamps else None
        last_timestamp = page_data.get("lastTimestamp")
        if not page_orders or (oldest is not None and oldest < start_timestamp):
            break
        if not last_timestamp or last_timestamp >= max_timestamp:
            raise RuntimeError("分页没有前进，停止以避免死循环")
        max_timestamp = last_timestamp
    else:
        raise RuntimeError(f"翻了 {MAX_PAGES} 页仍未结束，分页可能没有推进")

    selected = [
        order for order in orders.values()
        if order.get("startTimestamp") is not None
        and start_timestamp <= order["startTimestamp"] < timestamp_ms(end)
    ]
    selected.sort(key=lambda order: order["startTimestamp"], reverse=True)
    return selected, pages


def enrich(order: dict[str, Any]) -> dict[str, Any]:
    """Add Beijing-time and human-readable fields alongside the raw ones."""
    start_ms = order.get("startTimestamp")
    end_ms = order.get("endTimestamp")
    duration = (end_ms - start_ms) / 1000 if start_ms is not None and end_ms is not None else None
    result = dict(order)
    result["startTimeBeijing"] = local_datetime(start_ms)
    result["endTimeBeijing"] = local_datetime(end_ms)
    result["durationSeconds"] = duration
    result["durationText"] = duration_text(duration) if duration is not None else None
    for field, cents in (("originFeeYuan", "originFeeCent"),
                         ("actualFeeYuan", "actualFeeCent")):
        value = order.get(cents)
        result[field] = value / 100 if value is not None else None
    return result


def apply_detail(order: dict[str, Any], response: dict[str, Any] | None,
                 error: str = "") -> None:
    """Merge one detail response into an order, in place."""
    if response is None:
        order["detailStatus"] = "failed"
        if error:
            order["detailError"] = error
        return
    biz = ((response.get("data") or {}).get("bizData") or {})
    info = biz.get("mapInfo") or {}
    for field in DETAIL_FIELDS:
        order[field] = info.get(field)
    order["trackPointCount"] = track_point_count(info.get("trackPoints"))
    order["detailStatus"] = "ok"
    order["detailBizData"] = biz


def fetch_details(headers: list[str], orders: list[dict[str, Any]], raw_dir: Path,
                  retries: int, quiet: bool = False) -> None:
    """Fetch every order's detail, recording raw responses and failures."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    for index, order in enumerate(orders, start=1):
        order_id = str(order.get("orderId"))
        response, error = api.fetch_order_detail(headers, order_id, retries=retries)
        write_json(raw_dir / f"{index:03d}-{order_id}.json",
                   response if response is not None else {"error": error})
        apply_detail(order, response, error)
        if not quiet:
            mark = "ok" if response is not None else "失败"
            print(f"  详情 {index}/{len(orders)} {order_id} {mark}", file=sys.stderr)


def write_exports(orders: list[dict[str, Any]], pages: list[dict[str, Any]],
                  date_text: str, output_dir: Path) -> Path:
    total = sum((o.get("durationSeconds") or 0) for o in orders)
    summary = {
        "date": date_text,
        "timezone": "Asia/Shanghai",
        "orderCount": len(orders),
        "totalDurationSeconds": total,
        "totalDurationText": duration_text(total),
        "originFeeYuan": sum((o.get("originFeeYuan") or 0) for o in orders),
        "actualFeeYuan": sum((o.get("actualFeeYuan") or 0) for o in orders),
        "carbonEmissionsGram": sum((o.get("carbonEmissionsGram") or 0) for o in orders),
        "distanceMeterAvailable": any(o.get("distanceMeter") is not None for o in orders),
        "fullTrackOrderCount": sum(1 for o in orders
                                   if (o.get("trackPointCount") or 0) > 2),
        "pagesFetched": pages,
        "orders": orders,
    }
    json_path = output_dir / f"rides-{date_text}.json"
    write_json(json_path, summary)
    write_csv(output_dir / f"rides-{date_text}.csv", orders, CSV_FIELDS)
    return json_path


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    try:
        start, end = day_bounds(args.date)
    except ValueError as exc:
        raise SystemExit(f"日期格式错误：{exc}") from exc

    try:
        headers = api.credentials_from_har(args.har)
    except api.CredentialError as exc:
        raise SystemExit(str(exc)) from exc

    raw_root = args.output_dir / "raw"
    orders, pages = fetch_history(
        headers, start, end, args.page_size, raw_root / args.date)
    enriched = [enrich(order) for order in orders]

    if enriched and not args.skip_details:
        print(f"抓取 {len(enriched)} 笔订单详情…", file=sys.stderr)
        fetch_details(headers, enriched, raw_root / "details" / args.date,
                      args.detail_retries)

    json_path = write_exports(enriched, pages, args.date, args.output_dir)
    full_track = sum(1 for o in enriched if (o.get("trackPointCount") or 0) > 2)
    print(json.dumps({
        "date": args.date,
        "orders": len(enriched),
        "fullTrackOrders": full_track,
        "json": str(json_path),
        "csv": str(json_path.with_suffix(".csv")),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
