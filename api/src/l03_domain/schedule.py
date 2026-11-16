from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TZ = "Asia/Shanghai"

KIND_DAILY = "daily"
KIND_WEEKLY = "weekly"
KIND_MONTHLY = "monthly"
RULE_KINDS = (KIND_DAILY, KIND_WEEKLY, KIND_MONTHLY)
ALL_WEEKDAYS = (1, 2, 3, 4, 5, 6, 7)
# A model carries at most this many rules; the hot path compares every rule
# against the current moment, so the count stays bounded.
MAX_RULES = 10
# Monthly windows need a horizon that reaches the next occurrence of a
# day-31 rule, so "next available" stays computable for every rule kind.
HORIZON_DAYS = 62


def is_known_timezone(name: str) -> bool:
    """True when ``name`` is an IANA zone available to this server."""
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return False
    return True


def _zone(tz_name: str) -> ZoneInfo:
    """Resolve a stored zone name, falling back instead of failing a request."""
    return ZoneInfo(tz_name if is_known_timezone(tz_name) else DEFAULT_TZ)


@dataclass(frozen=True, slots=True)
class Segment:
    """One weekly recurring offline interval. ``end <= start`` crosses midnight."""

    days: tuple[int, ...]
    start: time
    end: time


@dataclass(frozen=True, slots=True)
class Rule:
    """One scheduled offline window.

    ``daily`` runs every day, ``weekly`` on the ISO weekdays in ``days``
    (Monday = 1) and ``monthly`` on the days of month in ``days_of_month``.
    ``end <= start`` crosses midnight and still belongs to the start day.
    """

    kind: str
    days: tuple[int, ...]
    days_of_month: tuple[int, ...]
    start: time
    end: time

    def matches_day(self, day: datetime) -> bool:
        if self.kind == KIND_DAILY:
            return True
        if self.kind == KIND_WEEKLY:
            return day.isoweekday() in self.days
        # A day the month does not have (the 31st of a 30-day month, the 29th
        # of a common February) simply never matches.
        return day.day in self.days_of_month


@dataclass(frozen=True, slots=True)
class Schedule:
    """The parsed offline windows of one model, in the schedule timezone."""

    timezone: str
    rules: tuple[Rule, ...]

    @property
    def segments(self) -> tuple[Segment, ...]:
        """Legacy v1 projection of the rules (weekly and daily only).

        Monthly rules have no v1 equivalent and are not projected; the
        authoritative list is ``rules``.
        """
        projected: list[Segment] = []
        for rule in self.rules:
            if rule.kind == KIND_MONTHLY:
                continue
            days = ALL_WEEKDAYS if rule.kind == KIND_DAILY else rule.days
            projected.append(Segment(days=tuple(days), start=rule.start, end=rule.end))
        return tuple(projected)


def _parse_rule(item: Any) -> Rule | None:
    """Parse one v2 rule; invalid entries are skipped instead of failing."""
    if not isinstance(item, dict):
        return None
    kind = str(item.get("kind") or KIND_WEEKLY).strip().lower()
    if kind not in RULE_KINDS:
        return None
    days: tuple[int, ...] = ()
    days_of_month: tuple[int, ...] = ()
    if kind == KIND_WEEKLY:
        collected: set[int] = set()
        for value in item.get("days") or []:
            try:
                day = int(value)
            except (TypeError, ValueError):
                continue
            if 1 <= day <= 7:
                collected.add(day)
        if not collected:
            return None
        days = tuple(sorted(collected))
    elif kind == KIND_MONTHLY:
        collected = set()
        for value in item.get("days_of_month") or []:
            try:
                day = int(value)
            except (TypeError, ValueError):
                continue
            if 1 <= day <= 31:
                collected.add(day)
        if not collected:
            return None
        days_of_month = tuple(sorted(collected))
    try:
        start = time.fromisoformat(str(item.get("start") or "00:00"))
        end = time.fromisoformat(str(item.get("end") or "00:00"))
    except ValueError:
        return None
    return Rule(kind=kind, days=days, days_of_month=days_of_month, start=start, end=end)


def _parse_segment_rule(item: Any) -> Rule | None:
    """Parse one legacy v1 segment into a weekly rule."""
    if not isinstance(item, dict):
        return None
    collected: set[int] = set()
    for value in item.get("days") or []:
        try:
            day = int(value)
        except (TypeError, ValueError):
            continue
        if 1 <= day <= 7:
            collected.add(day)
    if not collected:
        return None
    try:
        start = time.fromisoformat(str(item.get("start") or "00:00"))
        end = time.fromisoformat(str(item.get("end") or "00:00"))
    except ValueError:
        return None
    return Rule(
        kind=KIND_WEEKLY,
        days=tuple(sorted(collected)),
        days_of_month=(),
        start=start,
        end=end,
    )


def parse_schedule(raw: Any, default_tz: str = DEFAULT_TZ) -> Schedule:
    """Parse a v2 ``{version, timezone, rules:[...]}`` or a legacy v1
    ``{timezone, segments:[...]}`` document.

    Unknown fields are ignored and invalid entries are skipped instead of
    failing the request, so an old document keeps working unchanged.
    """
    if not isinstance(raw, dict):
        return Schedule(default_tz, ())
    tz = str(raw.get("timezone") or default_tz)
    if not is_known_timezone(tz):
        tz = default_tz
    rules: list[Rule] = []
    raw_rules = raw.get("rules")
    if isinstance(raw_rules, list) and raw_rules:
        for item in raw_rules:
            rule = _parse_rule(item)
            if rule is not None:
                rules.append(rule)
    else:
        for item in raw.get("segments") or []:
            rule = _parse_segment_rule(item)
            if rule is not None:
                rules.append(rule)
    return Schedule(tz, tuple(rules[:MAX_RULES]))


@lru_cache(maxsize=512)
def _parse_schedule_cached(raw_text: str, default_tz: str) -> Schedule:
    try:
        raw = json.loads(raw_text or "{}")
    except Exception:
        raw = {}
    return parse_schedule(raw, default_tz)


def parse_schedule_cached(raw_text: str, default_tz: str = DEFAULT_TZ) -> Schedule:
    """Parse a stored schedule document, reusing the parse per document.

    The hot path evaluates a model schedule on every request, so the parsed
    form is cached by the document text: a changed document is a new key.
    """
    return _parse_schedule_cached(raw_text, default_tz)


def _is_nonexistent(value: datetime) -> bool:
    """True when the local clock never shows this wall time (DST forward jump)."""
    if value.tzinfo is None:
        return False
    return value.astimezone(timezone.utc).astimezone(value.tzinfo) != value


def _day_start(value: datetime) -> datetime:
    return value.replace(hour=0, minute=0, second=0, microsecond=0)


def _rule_bounds(day: datetime, rule: Rule) -> tuple[datetime, datetime] | None:
    """The window of one rule anchored on ``day``, or None when it has none.

    A start the local clock skips (the spring-forward gap) leaves the day
    uncovered; a window that crosses midnight belongs to its start day.
    """
    start = day.replace(
        hour=rule.start.hour,
        minute=rule.start.minute,
        second=0,
        microsecond=0,
    )
    if _is_nonexistent(start):
        return None
    end = day.replace(
        hour=rule.end.hour,
        minute=rule.end.minute,
        second=0,
        microsecond=0,
    )
    if end <= start:
        end += timedelta(days=1)
    return start, end


def _current_window(now: datetime, schedule: Schedule) -> tuple[datetime, datetime] | None:
    """The window covering ``now``, looking at today and yesterday.

    A window that crosses midnight belongs to its start day, so yesterday's
    window can still be running now.
    """
    for offset in (0, -1):
        day = _day_start(now + timedelta(days=offset))
        for rule in schedule.rules:
            if not rule.matches_day(day):
                continue
            bounds = _rule_bounds(day, rule)
            if bounds is None:
                continue
            start, end = bounds
            if start <= now < end:
                return start, end
    return None


def _windows(
    now: datetime, schedule: Schedule, *, horizon_days: int = HORIZON_DAYS
) -> list[tuple[datetime, datetime]]:
    """Every window anchored between yesterday and the horizon end."""
    windows: list[tuple[datetime, datetime]] = []
    first = _day_start(now - timedelta(days=1))
    for offset in range(horizon_days + 2):
        day = first + timedelta(days=offset)
        for rule in schedule.rules:
            if not rule.matches_day(day):
                continue
            bounds = _rule_bounds(day, rule)
            if bounds is None:
                continue
            windows.append(bounds)
    windows.sort()
    return windows


def is_offline(now_ms: int, schedule: Schedule) -> bool:
    """Whether now falls inside any scheduled window."""
    if not schedule.rules:
        return False
    now = datetime.fromtimestamp(now_ms / 1000, _zone(schedule.timezone))
    return _current_window(now, schedule) is not None


def current_window(now_ms: int, schedule: Schedule) -> tuple[int, int] | None:
    """The window covering now as ``(start_ms, end_ms)``, or None when free."""
    if not schedule.rules:
        return None
    now = datetime.fromtimestamp(now_ms / 1000, _zone(schedule.timezone))
    bounds = _current_window(now, schedule)
    if bounds is None:
        return None
    start, end = bounds
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def next_available_ms(now_ms: int, schedule: Schedule) -> int | None:
    """Earliest moment >= now_ms at which the model is not in a scheduled window.

    ``None`` means every moment the horizon can prove is still offline (a
    window that covers the whole horizon, e.g. a whole-week window).
    """
    if not schedule.rules:
        return now_ms
    now = datetime.fromtimestamp(now_ms / 1000, _zone(schedule.timezone))
    horizon_end = now + timedelta(days=HORIZON_DAYS)
    cursor = now
    for start, end in _windows(now, schedule):
        if end <= cursor:
            continue
        if start > cursor:
            # A gap in the windows: the cursor is free.
            break
        cursor = end
        if cursor > horizon_end:
            # Still offline beyond the horizon; no provable free moment.
            return None
    return int(cursor.timestamp() * 1000)


def rule_to_dict(rule: Rule) -> dict[str, Any]:
    """One rule in the wire shape used by the API."""
    data: dict[str, Any] = {
        "kind": rule.kind,
        "start": rule.start.strftime("%H:%M"),
        "end": rule.end.strftime("%H:%M"),
    }
    if rule.kind == KIND_MONTHLY:
        data["days_of_month"] = list(rule.days_of_month)
    else:
        data["days"] = list(ALL_WEEKDAYS if rule.kind == KIND_DAILY else rule.days)
    return data


def rules_to_dict(schedule: Schedule) -> dict[str, Any]:
    """The schedule in the v2 document shape (authoritative)."""
    return {
        "version": 2,
        "timezone": schedule.timezone,
        "rules": [rule_to_dict(rule) for rule in schedule.rules],
    }


def schedule_to_dict(schedule: Schedule) -> dict[str, Any]:
    """The schedule in the legacy v1 shape (weekly projection only)."""
    return {
        "timezone": schedule.timezone,
        "segments": [
            {"days": list(s.days), "start": s.start.strftime("%H:%M"), "end": s.end.strftime("%H:%M")}
            for s in schedule.segments
        ],
    }
