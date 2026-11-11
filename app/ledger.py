from __future__ import annotations

from datetime import datetime, timedelta, timezone

_CST_FALLBACK = timezone(timedelta(hours=8))


def resolve_timezone(name: str):
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:
        return _CST_FALLBACK


def quota_date(now: datetime, tz_name: str) -> str:
    return now.astimezone(resolve_timezone(tz_name)).date().isoformat()
