from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def dt_to_ms(value: datetime | None) -> int | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp() * 1000)


def ms_to_dt(value: int | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value / 1000, timezone.utc)


def ms_to_local(value: int, tz_name: str = "Asia/Shanghai") -> datetime:
    return datetime.fromtimestamp(value / 1000, ZoneInfo(tz_name))


def day_start_ms(value_ms: int, tz_name: str = "Asia/Shanghai") -> int:
    local = ms_to_local(value_ms, tz_name)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(start.timestamp() * 1000)


def parse_range(range_name: str, start_ms: int | None, end_ms: int | None, tz_name: str = "Asia/Shanghai") -> tuple[int, int]:
    """Return [start, end) UTC milliseconds for a named range."""
    now = now_ms()
    if range_name == "today":
        return day_start_ms(now, tz_name), now
    if range_name == "24h":
        return now - 86_400_000, now
    if range_name == "7d":
        return now - 7 * 86_400_000, now
    if range_name == "30d":
        return now - 30 * 86_400_000, now
    if range_name == "custom":
        if start_ms is None or end_ms is None:
            raise ValueError("custom range requires start_ms and end_ms")
        if end_ms <= start_ms:
            raise ValueError("end_ms must be greater than start_ms")
        return start_ms, end_ms
    raise ValueError(f"unknown range: {range_name}")


def next_local_midnight_ms(value_ms: int, tz_name: str) -> int:
    local = ms_to_local(value_ms, tz_name)
    tomorrow = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return int(tomorrow.timestamp() * 1000)
