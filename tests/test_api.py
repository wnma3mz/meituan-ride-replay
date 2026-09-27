"""Tests for endpoint definitions and HAR credential extraction.

The HAR path is the only place credentials are handled, so it gets tested for
what it extracts *and* for what it must leave behind.
"""
from __future__ import annotations

import json

import pytest

from ridedata import api


def har(entries):
    return {"log": {"version": "1.2", "entries": entries}}


def entry(url, headers, started="2026-09-27T10:00:00.000Z", cookies=None):
    return {
        "startedDateTime": started,
        "request": {
            "url": url,
            "method": "POST",
            "headers": [{"name": k, "value": v} for k, v in headers.items()],
            "cookies": cookies or [],
        },
    }


def write(tmp_path, document):
    path = tmp_path / "capture.har"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


HISTORY = "https://bike.meituan.com/api/ride/assist/userProfile/ridingHistoryInfo.do"


class TestEndpoints:
    def test_urls_are_absolute_https(self):
        for url in (api.HISTORY_URL, api.DETAIL_URL):
            assert url.startswith("https://")

    def test_history_marker_matches_its_url(self):
        assert api.HISTORY_MARKER in api.HISTORY_URL


class TestCredentialExtraction:
    def test_prefers_the_history_request(self, tmp_path):
        path = write(tmp_path, har([
            entry("https://example.com/other", {"Cookie": "a=1"}),
            entry(HISTORY, {"Cookie": "token=real", "userid": "42"}),
        ]))
        headers = api.credentials_from_har(path)
        assert "-H" in headers
        joined = " ".join(headers)
        assert "userid: 42" in joined

    def test_falls_back_to_any_cookie_request(self, tmp_path):
        """Captures often lack the history call itself."""
        path = write(tmp_path, har([
            entry("https://example.com/telemetry",
                  {"Cookie": "token=real", "userid": "7", "yodaready": "1"}),
        ]))
        joined = " ".join(api.credentials_from_har(path))
        assert "userid: 7" in joined

    def test_ranks_by_session_header_count(self, tmp_path):
        path = write(tmp_path, har([
            entry("https://example.com/a", {"Cookie": "a=1"}),
            entry("https://example.com/b",
                  {"Cookie": "a=1", "userid": "9", "yodaready": "1",
                   "yodaversion": "2", "platinfo": "x"}),
        ]))
        joined = " ".join(api.credentials_from_har(path))
        assert "userid: 9" in joined

    def test_merges_cookies_across_entries(self, tmp_path):
        """Session and device cookies are often split across requests."""
        path = write(tmp_path, har([
            entry("https://example.com/a", {"Cookie": "session=s1"}),
            entry(HISTORY, {"Cookie": "device=d1", "userid": "1"}),
        ]))
        joined = " ".join(api.credentials_from_har(path))
        assert "session=s1" in joined
        assert "device=d1" in joined

    def test_picks_up_structured_cookies(self, tmp_path):
        path = write(tmp_path, har([
            entry(HISTORY, {"userid": "1"},
                  cookies=[{"name": "token", "value": "abc"}]),
        ]))
        joined = " ".join(api.credentials_from_har(path))
        assert "token=abc" in joined

    def test_drops_headers_curl_must_set_itself(self, tmp_path):
        path = write(tmp_path, har([
            entry(HISTORY, {
                "Cookie": "a=1",
                "Host": "bike.meituan.com",
                "Content-Length": "42",
                "Accept-Encoding": "gzip",
                "Connection": "keep-alive",
                "If-None-Match": "etag",
                "If-Modified-Since": "yesterday",
            }),
        ]))
        joined = " ".join(api.credentials_from_har(path)).lower()
        for banned in ("host:", "content-length:", "accept-encoding:",
                       "connection:", "if-none-match:", "if-modified-since:"):
            assert banned not in joined

    def test_keeps_the_cookie(self, tmp_path):
        path = write(tmp_path, har([entry(HISTORY, {"Cookie": "token=keepme"})]))
        assert "token=keepme" in " ".join(api.credentials_from_har(path))

    def test_headers_are_curl_dash_h_pairs(self, tmp_path):
        path = write(tmp_path, har([entry(HISTORY, {"Cookie": "a=1", "userid": "2"})]))
        headers = api.credentials_from_har(path)
        assert len(headers) % 2 == 0
        assert all(headers[i] == "-H" for i in range(0, len(headers), 2))


class TestCredentialErrors:
    def test_no_cookie_anywhere(self, tmp_path):
        path = write(tmp_path, har([entry("https://example.com/x", {"accept": "*/*"})]))
        with pytest.raises(api.CredentialError, match="Cookie"):
            api.credentials_from_har(path)

    def test_empty_har(self, tmp_path):
        path = write(tmp_path, har([]))
        with pytest.raises(api.CredentialError, match="没有任何请求"):
            api.credentials_from_har(path)

    def test_missing_file(self, tmp_path):
        with pytest.raises(api.CredentialError):
            api.credentials_from_har(tmp_path / "nope.har")

    def test_corrupt_json(self, tmp_path):
        path = tmp_path / "bad.har"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(api.CredentialError):
            api.credentials_from_har(path)

    def test_error_mentions_how_to_recover(self, tmp_path):
        path = write(tmp_path, har([entry("https://x/y", {"accept": "*/*"})]))
        with pytest.raises(api.CredentialError) as info:
            api.credentials_from_har(path)
        assert "HAR" in str(info.value)
