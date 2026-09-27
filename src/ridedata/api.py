"""Meituan bike endpoints, and credential extraction from a HAR capture.

The endpoints, request bodies, paging and retry behaviour are defined here in
code.  A HAR contributes exactly one thing: the credential headers of a
logged-in session (Cookie plus the user/device headers the bike API requires).

This replaces the older arrangement where a `request.curl` file carried both the
credentials *and* the URL, request body and curl flags, so a change to any of
them meant re-copying the file by hand.
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

BASE = "https://bike.meituan.com/api/ride"
HISTORY_URL = f"{BASE}/assist/userProfile/ridingHistoryInfo.do"
DETAIL_URL = f"{BASE}/core/std/querySettePage.do"

# Identifies the history request inside a HAR when one is present.
HISTORY_MARKER = "ridingHistoryInfo.do"

# Headers that must not be replayed: curl sets them, or they would trigger a 304.
SKIP_HEADERS = {
    "host", "content-length", "accept-encoding", "connection",
    "if-none-match", "if-modified-since",
}

# Headers whose presence marks an entry as carrying a usable bike-API session.
SESSION_HEADERS = {"userid", "yodaready", "yodaversion", "platinfo"}

HISTORY_TIMEOUT = 60
DETAIL_TIMEOUT = 40


class CredentialError(ValueError):
    """The HAR carried no reusable logged-in session."""


def _entries(document: dict[str, Any]) -> list[dict[str, Any]]:
    return document.get("log", {}).get("entries", []) or []


def _merged_cookies(entries: list[dict[str, Any]]) -> str:
    """Merge every Cookie seen in the capture.

    Cookies are often split across requests: a telemetry call may carry the
    session cookie while the map call carries the device headers.  Taking the
    union of all of them is what makes a capture from ordinary browsing usable.
    """
    values: dict[str, str] = {}
    for entry in entries:
        request = entry.get("request", {})
        for header in request.get("headers", []):
            if header.get("name", "").lower() == "cookie":
                for piece in header.get("value", "").split(";"):
                    if "=" in piece:
                        name, value = piece.strip().split("=", 1)
                        values.setdefault(name, value)
        for cookie in request.get("cookies", []):
            if cookie.get("name") and cookie.get("value") is not None:
                values.setdefault(cookie["name"], cookie["value"])
    return "; ".join(f"{name}={value}" for name, value in values.items())


def credentials_from_har(har_file: Path) -> list[str]:
    """Extract credential headers as curl `-H` arguments.

    Prefers the history request itself; failing that, any request carrying a
    Cookie, ranked by how many session headers it has and how recent it is.
    """
    try:
        document = json.loads(har_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CredentialError(f"无法读取 HAR {har_file}：{exc}") from exc

    entries = _entries(document)
    if not entries:
        raise CredentialError(f"{har_file} 没有任何请求记录")

    candidates = [
        entry for entry in entries
        if HISTORY_MARKER in entry.get("request", {}).get("url", "")
    ]
    if not candidates:
        candidates = [
            entry for entry in entries
            if any(h.get("name", "").lower() == "cookie"
                   for h in entry.get("request", {}).get("headers", []))
        ]
    if not candidates:
        raise CredentialError(
            f"{har_file} 里找不到带 Cookie 的请求；"
            "请在已登录的美团骑行页面重新抓一份 HAR")

    source = max(
        candidates,
        key=lambda entry: (
            sum(h.get("name", "").lower() in SESSION_HEADERS
                for h in entry.get("request", {}).get("headers", [])),
            entry.get("startedDateTime", ""),
        ),
    )

    by_name = {
        header.get("name", "").lower(): header
        for header in source["request"].get("headers", [])
    }
    cookies = _merged_cookies(entries)
    if cookies:
        by_name["cookie"] = {"name": "Cookie", "value": cookies}
    if "cookie" not in by_name:
        raise CredentialError(f"{har_file} 的请求里没有 Cookie")

    headers: list[str] = []
    for header in by_name.values():
        name = header.get("name", "")
        if name.lower() in SKIP_HEADERS:
            continue
        headers.extend(["-H", f"{name}: {header.get('value', '')}"])
    return headers


def _curl(url: str, headers: list[str], body: str, timeout: int) -> list[str]:
    return [
        "curl", url, *headers,
        "--data", body,
        "--silent", "--show-error", "--compressed",
        "--max-time", str(timeout),
    ]


def fetch_history_page(headers: list[str], max_timestamp: int,
                       page_size: int) -> dict[str, Any]:
    """One page of the history list, newest first, paging via max_timestamp."""
    body = f"querySize={page_size}&maxTimestamp={max_timestamp}&source=ORDER_LIST"
    command = _curl(HISTORY_URL, headers, body, HISTORY_TIMEOUT)
    done = subprocess.run(command, check=True, capture_output=True,
                          text=True, timeout=HISTORY_TIMEOUT + 15)
    try:
        response = json.loads(done.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"接口返回的不是 JSON：{done.stdout[:300]!r}\n"
            "通常是 HAR 的短期签名已过期，重新抓一份即可") from exc
    if response.get("code") != 0:
        raise RuntimeError(
            f"接口报错 {response.get('code')}: {response.get('message')}")
    return response


def fetch_order_detail(headers: list[str], order_id: str,
                       retries: int = 3) -> tuple[dict[str, Any] | None, str]:
    """One order's detail.  Returns (response, error_text).

    Retries with linear backoff; a failure is returned rather than raised so a
    bulk run records it and keeps going.
    """
    body = f"orderId={order_id}&scanRequestId="
    command = _curl(DETAIL_URL, headers, body, DETAIL_TIMEOUT)
    last_error = ""
    attempts = max(1, retries)
    for attempt in range(attempts):
        try:
            done = subprocess.run(command, check=True, capture_output=True,
                                  text=True, timeout=DETAIL_TIMEOUT + 10)
            response = json.loads(done.stdout)
            if response.get("code") == 0:
                return response, ""
            last_error = f"接口报错 {response.get('code')}: {response.get('message')}"
        except (subprocess.SubprocessError, json.JSONDecodeError) as exc:
            last_error = str(exc)
        if attempt + 1 < attempts:
            time.sleep(1.0 * (attempt + 1))
    return None, last_error
