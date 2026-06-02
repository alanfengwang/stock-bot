"""
recurring_invest.py — 定投调度小工具
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

NEW_YORK_TZ = ZoneInfo("America/New_York")


def current_new_york_time() -> datetime:
    return datetime.now(NEW_YORK_TZ)


def week_marker(dt: datetime) -> str:
    iso = dt.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def should_execute_weekly_dca(
    now_et: datetime,
    last_marker: str,
    weekday: int,
    min_hour: int,
) -> bool:
    if now_et.weekday() != weekday:
        return False
    if now_et.hour < min_hour:
        return False
    return week_marker(now_et) != last_marker
