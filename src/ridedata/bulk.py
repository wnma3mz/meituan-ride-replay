#!/usr/bin/env python3
"""Fetch and organize several years of Meituan bike orders.

The history list is fetched once and cached.  Order details are fetched only for
days a selection rule picks out, because details cost one request each.

    ride-data bulk --har capture.har
    ride-data bulk --har capture.har --filter real-gps
    ride-data bulk --har capture.har --list-filters

The only input is a HAR from a logged-in session; endpoints and paging live in
`api.py`.  Credentials are never written to any output file.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from . import api
from .common import (
    BEIJING,
    haversine_lonlat,
    read_json,
    write_csv,
    write_json,
)
from .fetch import apply_detail, enrich, timestamp_ms
from .filters import (
    DEFAULT_RULE,
    DayMetrics,
    FilterError,
    describe_rules,
    get_rule,
)
from .paths import filters_file, three_years

MAX_PAGES = 1000
CONNECT_METERS = 500
CONNECT_MAX_GAP_SECONDS = 7200


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--har", type=Path,
        help="从已登录会话导出的 HAR，只用于提取凭证",
    )
    parser.add_argument("--output-dir", type=Path, default=three_years())
    parser.add_argument(
        "--filter", dest="filter_name", default=DEFAULT_RULE,
        help=f"选哪些日期抓详情的规则名，默认 {DEFAULT_RULE}",
    )
    parser.add_argument(
        "--filters-file", type=Path, default=None,
        help="筛选规则 YAML，默认读项目根目录的 filters.yaml",
    )
    parser.add_argument(
        "--list-filters", action="store_true",
        help="列出可用筛选规则后退出",
    )
    parser.add_argument(
        "--refresh-history", action="store_true",
        help="忽略 all-orders.json 缓存，重新抓历史列表",
    )
    parser.add_argument("--years", type=int, default=3)
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--detail-retries", type=int, default=3)
    parser.add_argument(
        "--refresh-details", action="store_true",
        help="忽略已有订单详情缓存，重新请求每笔订单详情",
    )
    parser.add_argument("--sleep-seconds", type=float, default=0.25)
    parser.add_argument("--from-date", help="覆盖起始日期，YYYY-MM-DD")
    parser.add_argument("--to-date", help="覆盖结束日期，YYYY-MM-DD")
    return parser.parse_args(argv)


def date_range(args: argparse.Namespace) -> tuple[dt.date, dt.date]:
    today = dt.datetime.now(BEIJING).date()
    start = today.replace(year=today.year - args.years)
    end = today
    if args.from_date:
        start = dt.date.fromisoformat(args.from_date)
    if args.to_date:
        end = dt.date.fromisoformat(args.to_date)
    if start > end:
        raise ValueError("start date must not be after end date")
    return start, end


def fetch_history(
    headers: list[str],
    start: dt.date,
    end: dt.date,
    page_size: int,
    raw_dir: Path,
    sleep_seconds: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    start_ms = timestamp_ms(dt.datetime.combine(start, dt.time.min, BEIJING))
    end_ms = timestamp_ms(dt.datetime.combine(end + dt.timedelta(days=1), dt.time.min, BEIJING))
    max_timestamp = end_ms - 1
    orders: dict[str, dict[str, Any]] = {}
    pages: list[dict[str, Any]] = []
    raw_dir.mkdir(parents=True, exist_ok=True)

    for page_number in range(1, MAX_PAGES + 1):
        response = api.fetch_history_page(headers, max_timestamp, page_size)
        page_data = response.get("data") or {}
        page_orders = page_data.get("ridingOrders") or []
        raw_path = raw_dir / f"page-{page_number:04d}.json"
        write_json(raw_path, response)
        pages.append({
            "page": page_number,
            "requestMaxTimestamp": max_timestamp,
            "lastTimestamp": page_data.get("lastTimestamp"),
            "returnedOrders": len(page_orders),
            "rawFile": str(raw_path),
        })
        for order in page_orders:
            order_id = str(order.get("orderId"))
            if order_id and order_id != "None":
                orders[order_id] = order
        timestamps = [o.get("startTimestamp") for o in page_orders if o.get("startTimestamp") is not None]
        oldest = min(timestamps) if timestamps else None
        if not page_orders or (oldest is not None and oldest < start_ms):
            break
        last_timestamp = page_data.get("lastTimestamp")
        if not last_timestamp or last_timestamp >= max_timestamp:
            raise RuntimeError("pagination did not move backward")
        max_timestamp = last_timestamp
        if sleep_seconds:
            time.sleep(sleep_seconds)
    else:
        raise RuntimeError(f"翻了 {MAX_PAGES} 页仍未结束")

    selected = [
        order for order in orders.values()
        if order.get("startTimestamp") is not None
        and start_ms <= order["startTimestamp"] < end_ms
    ]
    selected.sort(key=lambda order: order["startTimestamp"])
    return selected, pages


def order_day(order: dict[str, Any]) -> str:
    return dt.datetime.fromtimestamp(order["startTimestamp"] / 1000, BEIJING).date().isoformat()


haversine_m = haversine_lonlat


def _cached_detail(raw_dir: Path, order_id: str) -> tuple[Path, dict[str, Any]] | None:
    """Find a successful raw detail response for an order, if one exists."""
    suffix = f"-{order_id}.json"
    for path in sorted(raw_dir.glob("*.json")):
        if not path.name.endswith(suffix):
            continue
        document = read_json(path)
        if isinstance(document, dict) and document.get("code") == 0:
            return path, document
    return None


def enrich_order_details(
    orders: list[dict[str, Any]],
    headers: list[str],
    raw_dir: Path,
    retries: int,
    sleep_seconds: float,
    refresh_details: bool = False,
) -> list[dict[str, Any]]:
    """Fetch and merge details, reusing successful raw responses by order ID."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    enriched: list[dict[str, Any]] = []
    for index, order in enumerate(orders, 1):
        order_id = str(order["orderId"])
        raw_file = raw_dir / f"{index:04d}-{order_id}.json"
        response: dict[str, Any] | None = None
        error = ""
        cached = None if refresh_details else _cached_detail(raw_dir, order_id)
        if cached is not None:
            raw_file, response = cached
            mark = "缓存"
        else:
            response, error = api.fetch_order_detail(headers, order_id, retries=retries)
            write_json(raw_file, response if response is not None else {"error": error})
            mark = "ok" if response is not None else "失败"
        result = enrich(order)
        apply_detail(result, response, error)
        result["detailRawFile"] = str(raw_file)
        enriched.append(result)
        if sleep_seconds:
            time.sleep(sleep_seconds)
        # Keep bulk output concise while making cache reuse visible.
        if response is not None and mark == "缓存":
            print(f"  详情 {index}/{len(orders)} {order_id} {mark}", file=sys.stderr)
    return enriched


# `details/` is the long-lived order-detail cache.  Keep it in place when
# rotating the selected day manifests so changing filters does not refetch it.
SELECTION_ARTIFACTS = (
    "days", "summary.json", "qualifying-days.csv", "connected-groups.csv",
)


def _archive_name(root: Path) -> str:
    summary = read_json(root / "summary.json", default={}) or {}
    name = str(summary.get("filter") or "previous")
    candidate = root / "archive" / name
    index = 2
    while candidate.exists():
        candidate = root / "archive" / f"{name}-{index}"
        index += 1
    return candidate.name


def archive_previous_selection(root: Path) -> Path | None:
    """Move the previous filter output aside before writing a new selection."""
    sources = [root / name for name in SELECTION_ARTIFACTS if (root / name).exists()]
    if not sources:
        return None
    archive = root / "archive" / _archive_name(root)
    archive.mkdir(parents=True, exist_ok=True)
    for source in sources:
        shutil.move(str(source), str(archive / source.name))
    return archive


def connections(orders: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[list[str]]]:
    ordered = sorted(orders, key=lambda order: order["startTimestamp"])
    links: list[dict[str, Any]] = []
    groups: list[list[str]] = []
    current: list[str] = []
    for previous, following in zip(ordered, ordered[1:]):
        gap_seconds = (following["startTimestamp"] - previous["endTimestamp"]) / 1000
        distance = haversine_m(previous.get("endLonlat"), following.get("startLonlat"))
        connected = (
            distance is not None
            and distance <= CONNECT_METERS
            and 0 <= gap_seconds <= CONNECT_MAX_GAP_SECONDS
        )
        links.append({
            "fromOrderId": previous["orderId"],
            "toOrderId": following["orderId"],
            "gapSeconds": gap_seconds,
            "endToStartMeters": distance,
            "connected": connected,
        })
        if connected:
            if not current:
                current = [previous["orderId"]]
            current.append(following["orderId"])
        elif current:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return links, groups


ORDER_CSV_FIELDS = [
    "date", "orderId", "bikeId", "startTimeBeijing", "endTimeBeijing", "durationText",
    "durationSeconds", "originFeeYuan", "actualFeeYuan", "carbonEmissionsGram",
    "detailStatus", "startLonlat", "endLonlat", "trackPointCount", "showTrack", "detailRawFile",
]


def write_order_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    write_csv(path, rows, ORDER_CSV_FIELDS)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    filters_path = args.filters_file or filters_file()

    if args.list_filters:
        print(describe_rules(filters_path))
        return

    try:
        rule = get_rule(args.filter_name, filters_path)
    except FilterError as exc:
        raise SystemExit(str(exc)) from exc

    if not args.har:
        raise SystemExit("需要 --har：从已登录的美团骑行页面导出一份 HAR")
    try:
        headers = api.credentials_from_har(args.har)
    except api.CredentialError as exc:
        raise SystemExit(str(exc)) from exc

    start, end = date_range(args)
    root = args.output_dir
    root.mkdir(parents=True, exist_ok=True)
    cache_path = root / "all-orders.json"
    cached = None
    if cache_path.exists() and not args.refresh_history:
        candidate = json.loads(cache_path.read_text(encoding="utf-8"))
        if candidate.get("fromDate") == start.isoformat() and candidate.get("toDate") == end.isoformat():
            cached = candidate
    if cached:
        all_orders = cached.get("orders") or []
        pages = cached.get("pagesFetched") or []
        print(f"复用 all-orders.json 缓存（{len(all_orders)} 笔）", file=sys.stderr)
    else:
        all_orders, pages = fetch_history(
            headers, start, end, args.page_size,
            root / "raw-history", args.sleep_seconds,
        )
    enriched_orders = [enrich(order) for order in all_orders]
    by_day: dict[str, list[dict[str, Any]]] = {}
    for order in enriched_orders:
        by_day.setdefault(order_day(order), []).append(order)

    # The rule runs on list-level metrics.  Track provenance is not known yet
    # (that needs details), so a rule testing it selects nothing at this stage;
    # `ride-data select` re-runs rules once details exist.
    qualifying_days = []
    for day, orders in sorted(by_day.items()):
        metrics = DayMetrics.from_orders(day, orders)
        if not rule.matches(metrics):
            continue
        longest = max(orders, key=lambda order: order.get("durationSeconds") or 0)
        qualifying_days.append({
            "date": day,
            "orderCount": metrics.order_count,
            "totalDurationSeconds": metrics.total_seconds,
            "maxSingleDurationSeconds": metrics.max_single_seconds,
            "maxSingleDurationText": longest.get("durationText"),
            "filter": rule.name,
        })
    print(f"筛选规则 {rule.name}：{rule.describe()} → {len(qualifying_days)}/{len(by_day)} 天",
          file=sys.stderr)
    archived_selection = archive_previous_selection(root)
    if archived_selection:
        print(f"上一次筛选结果已归档到 {archived_selection}", file=sys.stderr)

    if qualifying_days:
        connected_rows: list[dict[str, Any]] = []
        for position, item in enumerate(qualifying_days, 1):
            day = item["date"]
            print(f"[{position}/{len(qualifying_days)}] {day} 抓 "
                  f"{len(by_day[day])} 笔详情…", file=sys.stderr)
            detailed = enrich_order_details(
                by_day[day], headers, root / "details" / day,
                args.detail_retries, args.sleep_seconds,
                refresh_details=args.refresh_details,
            )
            links, groups = connections(detailed)
            item["detailsFetched"] = len(detailed)
            item["detailsSucceeded"] = sum(row.get("detailStatus") == "ok" for row in detailed)
            item["connectedGroups"] = groups
            item["connectionCount"] = sum(link["connected"] for link in links)
            for group_index, group in enumerate(groups, 1):
                connected_rows.append({
                    "date": day,
                    "groupIndex": group_index,
                    "orderCount": len(group),
                    "orderIds": ",".join(group),
                })
            day_dir = root / "days"
            day_dir.mkdir(parents=True, exist_ok=True)
            write_json(day_dir / f"{day}.json", {
                "date": day,
                "filter": rule.name,
                "filterDescription": rule.describe(),
                "summary": item,
                "connections": links,
                "orders": detailed,
            })
            write_order_csv(day_dir / f"{day}.csv", [{"date": day, **row} for row in detailed])
        write_csv(root / "connected-groups.csv", connected_rows,
                  ["date", "groupIndex", "orderCount", "orderIds"])
    else:
        (root / "connected-groups.csv").write_text(
            "date,groupIndex,orderCount,orderIds\n", encoding="utf-8"
        )

    write_json(root / "all-orders.json", {
        "fromDate": start.isoformat(),
        "toDate": end.isoformat(),
        "orderCount": len(enriched_orders),
        "pagesFetched": pages,
        "orders": enriched_orders,
    })
    write_order_csv(root / "all-orders.csv",
                    [{"date": order_day(order), **order} for order in enriched_orders])
    write_csv(root / "qualifying-days.csv", qualifying_days, [
        "date", "orderCount", "totalDurationSeconds", "detailsFetched",
        "detailsSucceeded", "connectionCount",
    ])
    summary = {
        "fromDate": start.isoformat(),
        "toDate": end.isoformat(),
        "orderCount": len(enriched_orders),
        "dayCount": len(by_day),
        "filter": rule.name,
        "filterDescription": rule.describe(),
        "filterConditions": rule.conditions,
        "qualifyingDayCount": len(qualifying_days),
        "qualifyingDays": qualifying_days,
        "archivedPreviousSelection": str(archived_selection) if archived_selection else None,
        "note": (
            f"筛选规则 {rule.name}：{rule.describe()}。"
            f"连接判断：前单结束到后单起点直线距离 <= {CONNECT_METERS} 米，"
            f"且间隔在 0 到 {CONNECT_MAX_GAP_SECONDS // 3600} 小时内；"
            "仅作候选连接，不代表道路路径。"
        ),
    }
    write_json(root / "summary.json", summary)
    print(json.dumps({
        "fromDate": start.isoformat(), "toDate": end.isoformat(),
        "orders": len(enriched_orders), "days": len(by_day),
        "filter": rule.name,
        "qualifyingDays": len(qualifying_days),
        "archivedPreviousSelection": str(archived_selection) if archived_selection else None,
        "output": str(root),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
