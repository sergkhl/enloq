"""Authored calendar and date-reading cases for Reminder Schedules (ADR 58)."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from enloq.domain.reminders import (
    DigestSettings,
    ReminderRule,
    language_name,
    latest_occurrence,
    local_time,
    named_day,
    next_occurrence,
    timezones,
)
from pydantic import ValidationError

SETTINGS = DigestSettings()


def stamp(value):
    return datetime.fromisoformat(value).timestamp()


def rule(**values):
    values.setdefault("start", "2026-10-26")
    values.setdefault("timezone", "UTC")
    values.setdefault("language", "English")
    return ReminderRule(**values)


def days(selected, after, count=4):
    """The next ``count`` local delivery dates after a moment."""
    found = []
    while len(found) < count:
        due, day = next_occurrence(selected, after, SETTINGS)
        found.append(day)
        after = due
    return found


def test_once_delivers_on_its_date_and_then_never_again():
    once = rule(repeat="once", start="2026-11-03", delivery="exact", time="18:30")
    due, day = next_occurrence(once, stamp("2026-10-26T08:00:00+00:00"), SETTINGS)
    assert day == "2026-11-03" and due == stamp("2026-11-03T18:30:00+00:00")
    assert next_occurrence(once, due, SETTINGS) is None
    assert latest_occurrence(once, due + 60, SETTINGS) == (due, day)


def test_every_n_days_starts_on_its_date():
    every = rule(repeat="days", start="2026-10-28", interval=3)
    assert days(every, stamp("2026-10-26T00:00:00+00:00")) == [
        "2026-10-28",
        "2026-10-31",
        "2026-11-03",
        "2026-11-06",
    ]


def test_every_n_weeks_follows_its_start_week():
    # The start week holds Wednesday 28 October; Mondays every second week.
    every = rule(repeat="weeks", start="2026-10-28", interval=2, weekday=0)
    assert days(every, stamp("2026-10-20T00:00:00+00:00")) == [
        "2026-10-26",
        "2026-11-09",
        "2026-11-23",
        "2026-12-07",
    ]
    # A start week far ahead waits for it; one far behind keeps its rhythm.
    assert days(every, stamp("2027-06-01T00:00:00+00:00"), 1) == ["2027-06-07"]


@pytest.mark.parametrize("year,expected", [(2024, "2024-02-29"), (2025, "2025-02-28")])
def test_monthly_short_month_keeps_selected_date(year, expected):
    selected = rule(repeat="monthly", month_day=31, delivery="exact")
    feb, day = next_occurrence(
        selected, stamp(f"{year}-02-01T00:00:00+00:00"), SETTINGS
    )
    assert day == expected and selected.month_day == 31
    _, following = next_occurrence(selected, feb, SETTINGS)
    assert following == f"{year}-03-31"


def test_yearly_29_february_falls_on_the_28th_in_other_years():
    leap = rule(repeat="yearly", month=2, month_day=29)
    assert days(leap, stamp("2026-01-01T00:00:00+00:00"), 3) == [
        "2026-02-28",
        "2027-02-28",
        "2028-02-29",
    ]
    with pytest.raises(ValidationError):
        rule(repeat="yearly", month=4, month_day=31)


def test_weekly_saved_zone_digest_mapping_and_future_only():
    selected = rule(timezone="Asia/Tokyo", weekday=0)
    settings = DigestSettings(timezone="America/Los_Angeles", time="09:00")
    due_at, day = next_occurrence(
        selected, stamp("2026-10-25T00:00:00+00:00"), settings
    )
    assert day == "2026-10-26"
    assert (
        datetime.fromtimestamp(due_at, ZoneInfo(settings.timezone)).isoformat()
        == "2026-10-25T09:00:00-07:00"
    )
    assert next_occurrence(selected, due_at, settings)[1] == "2026-11-02"


def test_dst_gap_advances_to_first_valid_time_and_fold_has_one_occurrence():
    selected = rule(
        timezone="America/New_York",
        start="2026-03-01",
        weekday=6,
        delivery="exact",
        time="02:30",
    )
    spring, _ = next_occurrence(selected, stamp("2026-03-08T00:00:00-05:00"), SETTINGS)
    assert (
        datetime.fromtimestamp(spring, ZoneInfo(selected.timezone)).isoformat()
        == "2026-03-08T03:00:00-04:00"
    )
    selected = selected.model_copy(update={"time": "01:30"})
    autumn, _ = next_occurrence(selected, stamp("2026-11-01T00:00:00-04:00"), SETTINGS)
    assert datetime.fromtimestamp(autumn, ZoneInfo(selected.timezone)).fold == 0
    assert next_occurrence(selected, autumn, SETTINGS)[1] == "2026-11-08"


def test_digest_dst_gap():
    settings = DigestSettings(time="02:30", timezone="America/New_York")
    due_at = local_time(date(2026, 3, 8), settings.time, settings.timezone)
    assert datetime.fromtimestamp(due_at, ZoneInfo(settings.timezone)).hour == 3


def test_a_rule_needs_a_listed_language_a_real_date_and_plain_text():
    for values in (
        {"language": "Klingon"},
        {"start": "2026-02-30"},
        {"idea": "Bell\x07"},
        {"repeat": "weeks", "interval": 53},
        {"idea": "x" * 4097},
    ):
        with pytest.raises(ValidationError):
            rule(**values)
    assert rule(idea="Line one\nLine two [ask]").idea.endswith("[ask]")


@pytest.mark.parametrize(
    "value,expected",
    [
        ("English", "English"),
        ("en-US", "English"),
        ("pt_BR", "Portuguese"),
        ("Japanese", "Japanese"),
        ("Русский", "Russian"),
        ("ja", "Japanese"),
        ("no", "Norwegian"),
        ("Klingon", "English"),
        ("", "English"),
        (None, "English"),
    ],
)
def test_free_text_languages_map_to_the_list(value, expected):
    assert language_name(value) == expected


def test_the_timezone_list_is_searchable_iana_names():
    zones = timezones()
    assert zones[0] == "UTC" and "Europe/Berlin" in zones
    assert not any(zone.startswith(("Etc/", "posix/", "right/")) for zone in zones)


NEXT_DUE = date(2026, 11, 2)


@pytest.mark.parametrize(
    "idea,expected",
    [
        ("Ask how the oct 25 trip went", "2026-10-25"),
        ("Ask how the trip on 25 October went", "2026-10-25"),
        ("Remember October 25th, 2025?", "2025-10-25"),
        ("the 25th of October", "2026-10-25"),
        ("Спросить про 25 октября", "2026-10-25"),
        ("Спросить про 25 окт.", "2026-10-25"),
        ("Как прошло 9 мая 2026 года?", "2026-05-09"),
        ("plans for 25.10", "2026-10-25"),
        ("plans for 25.10.2024", "2024-10-25"),
        ("see 2026-10-25 notes", "2026-10-25"),
        # A yearless date still ahead of the next delivery means last year's.
        ("what happened on dec 31", "2025-12-31"),
        # The first date named wins.
        ("between 20 oct and 25 oct", "2026-10-20"),
        # 29 February means the latest leap year's.
        ("29 feb party", "2024-02-29"),
        # Relative words, decimals and month names without a day name none.
        ("what we said yesterday", None),
        ("a 3.5 km run", None),
        ("в октябре", None),
        ("in October", None),
        ("on 31.13", None),
        ("on 2026-02-30", None),
    ],
)
def test_explicit_dates_in_an_idea_name_one_day(idea, expected):
    found = named_day(idea, NEXT_DUE)
    assert (found.isoformat() if found else None) == expected
