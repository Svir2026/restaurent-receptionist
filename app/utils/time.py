from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _get_tz(tz_name: str) -> ZoneInfo:
    name = (tz_name or "").strip()
    if not name:
        raise ValueError("missing timezone; set RESTAURANT_TIMEZONE (e.g. Asia/Karachi)")

    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as e:
        raise ValueError(
            f"invalid timezone {name!r}; set RESTAURANT_TIMEZONE to a valid IANA name "
            f"(e.g. 'Asia/Karachi'). On Windows, install the 'tzdata' package."
        ) from e


def tz_now(tz_name: str) -> datetime:
    return datetime.now(tz=_get_tz(tz_name))


def make_window(now: datetime, lookahead_hours: int) -> tuple[datetime, datetime]:
    start = now - timedelta(minutes=10)
    end = now + timedelta(hours=lookahead_hours)
    return start, end


def coerce_to_tz(dt: datetime, tz_name: str) -> datetime:
    tz = _get_tz(tz_name)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)
    return dt.astimezone(tz)
