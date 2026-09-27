"""Text formatting for on-screen captions.

Kept here rather than imported from `ridedata` so the two packages stay
independent: the video pipeline runs against the data directory, not against the
fetcher's code.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

BEIJING = timezone(timedelta(hours=8))


def beijing_dt(milliseconds: float) -> datetime:
    return datetime.fromtimestamp(milliseconds / 1000, BEIJING)


def clock(milliseconds: float) -> str:
    return beijing_dt(milliseconds).strftime("%H:%M")


def duration_text(seconds: float) -> str:
    """Chinese duration text that keeps the hour component."""
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}小时{minutes:02d}分{secs:02d}秒"
    return f"{minutes}分{secs:02d}秒"


def duration_short(seconds: float) -> str:
    """Compact form for cramped captions: `6 小时 36 分` / `48 分`."""
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes = remainder // 60
    if hours:
        return f"{hours} 小时 {minutes} 分"
    return f"{minutes} 分"
