"""Calendar reminders, independent of message-cache and AI lifetimes (ADR 58)."""

import calendar
import re
from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from pydantic import Field, field_validator, model_validator

from .models import Model

# Occurrences are created this long before their alert, so their suggestions
# can be prepared in time (ADR 58 Q5); they become pending when due.
LEAD = 120
# New messages refresh a chat's reminder set after this many quiet seconds.
QUIET = 30
# A Message Idea is a draft at heart, bounded like a Telegram message.
IDEA_CHARACTERS = 4096
# Kept Messages: at most this many of the named day's cached messages.
KEPT = 20

# The fallback-language list (ADR 58 Q1): a code, the name the writer reads
# and the language's own name.
LANGUAGES = (
    ("ar", "Arabic", "العربية"),
    ("hy", "Armenian", "Հայերեն"),
    ("az", "Azerbaijani", "Azərbaycan"),
    ("be", "Belarusian", "Беларуская"),
    ("bn", "Bengali", "বাংলা"),
    ("bg", "Bulgarian", "Български"),
    ("ca", "Catalan", "Català"),
    ("zh", "Chinese", "中文"),
    ("hr", "Croatian", "Hrvatski"),
    ("cs", "Czech", "Čeština"),
    ("da", "Danish", "Dansk"),
    ("nl", "Dutch", "Nederlands"),
    ("en", "English", "English"),
    ("et", "Estonian", "Eesti"),
    ("fi", "Finnish", "Suomi"),
    ("fr", "French", "Français"),
    ("ka", "Georgian", "ქართული"),
    ("de", "German", "Deutsch"),
    ("el", "Greek", "Ελληνικά"),
    ("he", "Hebrew", "עברית"),
    ("hi", "Hindi", "हिन्दी"),
    ("hu", "Hungarian", "Magyar"),
    ("id", "Indonesian", "Bahasa Indonesia"),
    ("it", "Italian", "Italiano"),
    ("ja", "Japanese", "日本語"),
    ("kk", "Kazakh", "Қазақ"),
    ("ko", "Korean", "한국어"),
    ("lv", "Latvian", "Latviešu"),
    ("lt", "Lithuanian", "Lietuvių"),
    ("ms", "Malay", "Bahasa Melayu"),
    ("nb", "Norwegian", "Norsk"),
    ("fa", "Persian", "فارسی"),
    ("pl", "Polish", "Polski"),
    ("pt", "Portuguese", "Português"),
    ("ro", "Romanian", "Română"),
    ("ru", "Russian", "Русский"),
    ("sr", "Serbian", "Српски"),
    ("sk", "Slovak", "Slovenčina"),
    ("sl", "Slovenian", "Slovenščina"),
    ("es", "Spanish", "Español"),
    ("sw", "Swahili", "Kiswahili"),
    ("sv", "Swedish", "Svenska"),
    ("tl", "Tagalog", "Tagalog"),
    ("th", "Thai", "ไทย"),
    ("tr", "Turkish", "Türkçe"),
    ("uk", "Ukrainian", "Українська"),
    ("ur", "Urdu", "اردو"),
    ("uz", "Uzbek", "Oʻzbek"),
    ("vi", "Vietnamese", "Tiếng Việt"),
)
LANGUAGE_NAMES = frozenset(name for _, name, _ in LANGUAGES)
# Other spellings of the same languages a free-text field or a device held.
_ALIASES = {"no": "nb", "nn": "nb", "iw": "he", "in": "id", "fil": "tl"}


def language_name(value, default="English"):
    """A listed language for a free-text name, a code or a device tag such as
    "en-US"; anything unrecognised becomes ``default`` (schema 40)."""
    text = (value or "").strip()
    if not text:
        return default
    lowered = text.casefold()
    for code, name, native in LANGUAGES:
        if lowered in (name.casefold(), native.casefold()):
            return name
    tag = re.split(r"[-_]", lowered, maxsplit=1)[0]
    tag = _ALIASES.get(tag, tag)
    for code, name, _ in LANGUAGES:
        if tag == code:
            return name
    return default


_REGIONS = re.compile(
    r"^(?:Africa|America|Antarctica|Arctic|Asia|Atlantic|Australia|Europe|Indian|Pacific)/"
)


def timezones():
    """The searchable timezone list: IANA's regional names and UTC."""
    return ["UTC"] + sorted(z for z in available_timezones() if _REGIONS.match(z))


def valid_timezone(value):
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("Choose a valid timezone") from None
    return value


CLOCK = r"^(?:[01]\d|2[0-3]):[0-5]\d$"
DAY = r"^\d{4}-\d{2}-\d{2}$"


class DigestSettings(Model):
    revision: int = Field(default=0, ge=0)
    time: str = Field(default="09:00", pattern=CLOCK)
    timezone: str = Field(default="UTC", min_length=1, max_length=100)

    _zone = field_validator("timezone")(valid_timezone)


Repeat = Literal["once", "days", "weeks", "monthly", "yearly"]


class ReminderRule(Model):
    """One Reminder Schedule's rule (ADR 58 Q1). ``start`` is Once's date,
    the first date of Every N days and a date in the start week of Every N
    weeks; Monthly uses ``month_day`` and Yearly ``month`` with it."""

    repeat: Repeat = "weeks"
    start: str = Field(pattern=DAY)
    interval: int = Field(default=1, ge=1, le=365)
    weekday: int = Field(default=0, ge=0, le=6)
    month: int = Field(default=1, ge=1, le=12)
    month_day: int = Field(default=1, ge=1, le=31)
    timezone: str = Field(min_length=1, max_length=100)
    delivery: Literal["digest", "exact"] = "digest"
    time: str = Field(default="09:00", pattern=CLOCK)
    language: str = Field(min_length=1, max_length=80)
    # The Message Idea (Q10): a draft, [bracketed] instructions, or both.
    idea: str = Field(default="", max_length=IDEA_CHARACTERS)

    _zone = field_validator("timezone")(valid_timezone)

    @field_validator("language")
    @classmethod
    def listed(cls, value):
        if value not in LANGUAGE_NAMES:
            raise ValueError("Choose a fallback language from the list")
        return value

    @field_validator("idea")
    @classmethod
    def plain(cls, value):
        if any(ord(c) < 32 and c not in "\n\t" for c in value):
            raise ValueError("A Message Idea holds plain text")
        return value

    @model_validator(mode="after")
    def coherent(self):
        try:
            date.fromisoformat(self.start)
        except ValueError:
            raise ValueError("Choose a valid date") from None
        if self.repeat == "weeks" and self.interval > 52:
            raise ValueError("Repeat at most every 52 weeks")
        if (
            self.repeat == "yearly"
            and self.month_day > calendar.monthrange(2024, self.month)[1]
        ):
            raise ValueError("Choose a day this month has")
        return self


class ReminderEdit(ReminderRule):
    revision: int = Field(ge=0)


class ReminderRevision(Model):
    revision: int = Field(ge=0)


class ReminderAction(Model):
    occurrence: str = Field(pattern=r"^[a-f0-9]{32}$")


class ReminderOccurrence(Model):
    id: str
    due_at: float
    calendar_date: str


class KeptStatus(Model):
    """The editor's Kept Messages status line (ADR 58 Q11)."""

    day: str
    state: Literal["waiting", "kept", "expired"]
    count: int = 0


class ReminderView(Model):
    id: str
    revision: int
    schedule: ReminderRule
    next_at: float | None = None
    status: Literal["active", "paused", "unavailable"] = "active"
    # Only once due: an occurrence being prepared is not yet pending.
    occurrence: ReminderOccurrence | None = None
    kept: KeptStatus | None = None


class ChatReminders(Model):
    items: list[ReminderView]
    # The chat's recent language when known, a new schedule's fallback.
    language: str | None = None


class ReminderItem(ReminderView):
    account_id: int
    account: str
    peer: str
    title: str
    photo_id: str | None = None
    ai_enabled: bool


class ReminderList(Model):
    items: list[ReminderItem]


class ReminderPreview(Model):
    next_at: float | None
    # What saving would keep of the day the idea names (ADR 58 Q11).
    kept: KeptStatus | None = None


class PendingReminder(Model):
    """One pending occurrence for the Reminder Bar (ADR 58 Q13)."""

    schedule: str
    occurrence: str
    idea: str
    due_at: float


class Timezones(Model):
    timezones: list[str]


class Language(Model):
    code: str
    name: str
    native: str


class Languages(Model):
    languages: list[Language]


def first_line(text):
    return next((line.strip() for line in text.splitlines() if line.strip()), "")


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


def _in_month(year, month, day):
    """A selected date in a short month falls on its last day (Q1)."""
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _days(rule: ReminderRule, today: date):
    """Nearby calendar dates, bounded independently of downtime length."""
    start = date.fromisoformat(rule.start)
    if rule.repeat == "once":
        return [start]
    if rule.repeat in ("days", "weeks"):
        step = rule.interval * (1 if rule.repeat == "days" else 7)
        first = (
            start
            if rule.repeat == "days"
            else start - timedelta(days=start.weekday() - rule.weekday)
        )
        index = max(0, (today - first).days // step)
        return [
            first + timedelta(days=step * n)
            for n in range(max(0, index - 2), index + 4)
        ]
    if rule.repeat == "monthly":
        month = today.year * 12 + today.month - 1
        days = []
        for n in range(-2, 4):
            year, index = divmod(month + n, 12)
            days.append(_in_month(year, index + 1, rule.month_day))
        return days
    return [_in_month(today.year + n, rule.month, rule.month_day) for n in range(-2, 3)]


def occurrences(rule: ReminderRule, around: float, settings: DigestSettings):
    """Nearby occurrences as (due time, local calendar date), in order."""
    today = datetime.fromtimestamp(around, ZoneInfo(rule.timezone)).date()
    result = []
    for day in _days(rule, today):
        if rule.delivery == "exact":
            due = local_time(day, rule.time, rule.timezone)
        else:
            due = next_digest(
                local_time(day, "00:00", rule.timezone), settings, inclusive=True
            )
        result.append((due, day.isoformat()))
    return sorted(result)


def next_occurrence(rule: ReminderRule, after: float, settings: DigestSettings):
    """The next delivery after ``after``, or None when a Once date is past."""
    return next(
        (item for item in occurrences(rule, after, settings) if item[0] > after), None
    )


def latest_occurrence(rule: ReminderRule, now: float, settings: DigestSettings):
    """The latest delivery at or before ``now``, or None."""
    return max(
        (item for item in occurrences(rule, now, settings) if item[0] <= now),
        default=None,
    )


# Kept Messages read explicit dates only (ADR 58 Q11): English and Russian
# month names before or after the day, d.m and d.m.yyyy with dots, and ISO
# dates. Relative words such as "yesterday" name no day.
_ENGLISH = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}
_RUSSIAN = (
    (1, r"январ[ьяе]|янв"),
    (2, r"феврал[ьяе]|фев"),
    (3, r"марта?|марте|мар"),
    (4, r"апрел[ьяе]|апр"),
    (5, r"ма[йяе]"),
    (6, r"июн[ьяе]|июн"),
    (7, r"июл[ьяе]|июл"),
    (8, r"августа?|августе|авг"),
    (9, r"сентябр[ьяе]|сент|сен"),
    (10, r"октябр[ьяе]|окт"),
    (11, r"ноябр[ьяе]|нояб|ноя"),
    (12, r"декабр[ьяе]|дек"),
)
_MONTH = (
    "(?P<month>"
    + "|".join(sorted(_ENGLISH, key=len, reverse=True))
    + "|"
    + "|".join(pattern for _, pattern in _RUSSIAN)
    + r")\.?"
)
_DAY = r"(?P<day>\d{1,2})(?:st|nd|rd|th|-?го)?"
_YEAR = r"(?:,?\s+(?P<year>\d{4})(?:\s*(?:г\.|года?))?)?"
_DATES = (
    re.compile(r"(?<![\d.-])(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})(?![\d-])"),
    # A two-digit month keeps decimals such as "3.5 km" from reading as dates.
    re.compile(
        r"(?<![\d.])(?P<day>\d{1,2})\.(?P<month>\d{2})(?:\.(?P<year>\d{4}))?(?!\d|\.\d)"
    ),
    re.compile(rf"(?<![\w]){_DAY}\s+(?:of\s+)?{_MONTH}(?!\w){_YEAR}", re.IGNORECASE),
    re.compile(rf"(?<![\w]){_MONTH}\s+{_DAY}(?!\w){_YEAR}", re.IGNORECASE),
)


def _month(text):
    if text.isdigit():
        return int(text)
    word = text.casefold().rstrip(".")
    if word in _ENGLISH:
        return _ENGLISH[word]
    for number, pattern in _RUSSIAN:
        if re.fullmatch(pattern, word):
            return number
    return None


def _latest(month, day, on_or_before: date):
    """A yearless date's latest occurrence on or before a day; 29 February
    falls back to the latest leap year."""
    for year in range(on_or_before.year, on_or_before.year - 9, -1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        if candidate <= on_or_before:
            return candidate
    return None


def named_day(idea: str, next_due: date) -> date | None:
    """The first explicit date in a Message Idea, if any. A yearless date
    means its latest occurrence before the schedule is next due."""
    found = []
    for pattern in _DATES:
        for match in pattern.finditer(idea or ""):
            month = _month(match["month"])
            day = int(match["day"])
            year = match["year"]
            if not month or not 1 <= month <= 12 or not 1 <= day <= 31:
                continue
            if year:
                try:
                    found.append((match.start(), date(int(year), month, day)))
                except ValueError:
                    continue
            elif (latest := _latest(month, day, next_due)) is not None:
                found.append((match.start(), latest))
    return min(found)[1] if found else None


def day_bounds(day: date, zone: str):
    """The start and end of a local calendar day, as timestamps."""
    tz = ZoneInfo(zone)
    start = datetime.combine(day, time()).replace(tzinfo=tz).timestamp()
    end = (
        datetime.combine(day + timedelta(days=1), time()).replace(tzinfo=tz).timestamp()
    )
    return start, end
