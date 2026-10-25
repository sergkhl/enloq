"""Calendar reminders, independent of message-cache and AI lifetimes (ADR 58)."""

import calendar
from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator

from .models import Model


def valid_timezone(value):
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("Choose a valid timezone") from None
    return value


class DigestSettings(Model):
    revision: int = Field(default=0, ge=0)
    time: str = Field(default="09:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    timezone: str = Field(default="UTC", min_length=1, max_length=100)

    _zone = field_validator("timezone")(valid_timezone)


class ReminderRule(Model):
    frequency: Literal["weekly", "monthly"] = "weekly"
    weekday: int = Field(default=0, ge=0, le=6)
    month_day: int = Field(default=1, ge=1, le=31)
    timezone: str = Field(min_length=1, max_length=100)
    delivery: Literal["digest", "exact"] = "digest"
    time: str = Field(default="09:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    language: str = Field(min_length=1, max_length=80)

    _zone = field_validator("timezone")(valid_timezone)

    @field_validator("language")
    @classmethod
    def language_name(cls, value):
        if not value.strip() or any(ord(c) < 32 for c in value):
            raise ValueError("Choose a fallback language")
        return value.strip()


class ReminderEdit(ReminderRule):
    revision: int = Field(ge=0)


class ReminderRevision(Model):
    revision: int = Field(ge=0)


class ReminderAction(Model):
    occurrence: str = Field(pattern=r"^[a-f0-9]{32}$")


class ReminderReview(ReminderAction):
    revision: int = Field(ge=0)
    cursor: int | None = Field(default=None, ge=0)
    regenerate: bool = False


class ReminderOccurrence(Model):
    id: str
    due_at: float
    calendar_date: str
    reviewed: bool


class ReminderView(Model):
    revision: int = 0
    schedule: ReminderRule | None = None
    next_at: float | None = None
    status: Literal["active", "paused", "unavailable"] = "active"
    occurrence: ReminderOccurrence | None = None


class ReminderItem(ReminderView):
    account_id: int
    account: str
    peer: str
    title: str
    ai_enabled: bool


class ReminderList(Model):
    items: list[ReminderItem]


class ReminderPreview(Model):
    next_at: float


def local_time(day: date, clock: str, zone: str) -> float:
    """First real instance of a wall time. A gap advances to its first valid
    minute, rather than preserving the minutes through the offset change."""
    tz = ZoneInfo(zone)
    wall = datetime.combine(day, time.fromisoformat(clock))
    for _ in range(2881):
        candidate = wall.replace(tzinfo=tz, fold=0).timestamp()
        if datetime.fromtimestamp(candidate, tz).replace(tzinfo=None) == wall:
            return candidate
        wall += timedelta(minutes=1)
    raise ValueError("No valid calendar time")


def next_digest(after: float, settings: DigestSettings, *, inclusive=False) -> float:
    day = datetime.fromtimestamp(after, ZoneInfo(settings.timezone)).date()
    for offset in range(3):
        value = local_time(
            day + timedelta(days=offset), settings.time, settings.timezone
        )
        if value > after or (inclusive and value == after):
            return value
    raise ValueError("No next digest")


def occurrences(rule: ReminderRule, around: float, settings: DigestSettings):
    """Nearby calendar occurrences, bounded independently of downtime length."""
    today = datetime.fromtimestamp(around, ZoneInfo(rule.timezone)).date()
    if rule.frequency == "weekly":
        monday = today - timedelta(days=today.weekday())
        days = [monday + timedelta(days=rule.weekday + 7 * n) for n in range(-3, 4)]
    else:
        month = today.year * 12 + today.month - 1
        days = []
        for n in range(-2, 4):
            year, index = divmod(month + n, 12)
            last = calendar.monthrange(year, index + 1)[1]
            days.append(date(year, index + 1, min(rule.month_day, last)))
    result = []
    for day in days:
        if rule.delivery == "exact":
            due = local_time(day, rule.time, rule.timezone)
        else:
            due = next_digest(
                local_time(day, "00:00", rule.timezone), settings, inclusive=True
            )
        result.append((due, day.isoformat()))
    return sorted(result)


def next_occurrence(rule: ReminderRule, after: float, settings: DigestSettings):
    return next(item for item in occurrences(rule, after, settings) if item[0] > after)
