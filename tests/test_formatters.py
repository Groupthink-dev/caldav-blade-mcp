"""Tests for token-efficient output formatters."""

from __future__ import annotations

from caldav_blade_mcp.formatters import (
    format_calendar_list,
    format_event_detail,
    format_event_line,
    format_event_list,
    format_events_grouped,
    format_freebusy,
    format_info,
)


class TestFormatEventLine:
    def test_basic_event(self) -> None:
        event = {
            "uid": "abc123",
            "title": "Standup",
            "start": "2026-03-13T08:30:00+11:00",
            "end": "2026-03-13T09:30:00+11:00",
            "all_day": False,
        }
        result = format_event_line(event)
        assert "08:30-09:30" in result
        assert "Standup" in result
        assert "uid=abc123" in result

    def test_all_day_event(self) -> None:
        event = {
            "uid": "day1",
            "title": "Holiday",
            "start": "2026-03-13",
            "end": "2026-03-14",
            "all_day": True,
        }
        result = format_event_line(event)
        assert "All day" in result
        assert "Holiday" in result

    def test_event_with_location(self) -> None:
        event = {
            "uid": "loc1",
            "title": "Meeting",
            "start": "2026-03-13T14:00:00+11:00",
            "end": "2026-03-13T15:00:00+11:00",
            "all_day": False,
            "location": "Room 4B",
        }
        result = format_event_line(event)
        assert "location=Room 4B" in result

    def test_event_with_attendees(self) -> None:
        event = {
            "uid": "att1",
            "title": "Review",
            "start": "2026-03-13T10:00:00+11:00",
            "end": "2026-03-13T11:00:00+11:00",
            "all_day": False,
            "attendees": [
                {"name": "Alice", "email": "alice@example.com"},
                {"name": "Bob", "email": "bob@example.com"},
            ],
        }
        result = format_event_line(event)
        assert "attendees=Alice,Bob" in result

    def test_null_fields_omitted(self) -> None:
        event = {
            "uid": "min1",
            "title": "Quick sync",
            "start": "2026-03-13T09:00:00+11:00",
            "end": "2026-03-13T09:15:00+11:00",
            "all_day": False,
            "location": None,
            "attendees": None,
            "recurrence_rule": None,
        }
        result = format_event_line(event)
        assert "location" not in result
        assert "attendees" not in result
        assert "recurs" not in result

    def test_event_with_recurrence(self) -> None:
        event = {
            "uid": "rec1",
            "title": "Weekly standup",
            "start": "2026-03-13T09:00:00+11:00",
            "end": "2026-03-13T09:30:00+11:00",
            "all_day": False,
            "recurrence_rule": "FREQ=WEEKLY;BYDAY=MO",
        }
        result = format_event_line(event)
        assert "recurs=FREQ=WEEKLY;BYDAY=MO" in result


class TestFormatEventList:
    def test_empty(self) -> None:
        assert format_event_list([]) == "(no events)"

    def test_sorted_by_start(self) -> None:
        events = [
            {
                "uid": "b",
                "title": "Later",
                "start": "2026-03-13T14:00:00+11:00",
                "end": "2026-03-13T15:00:00+11:00",
                "all_day": False,
            },
            {
                "uid": "a",
                "title": "Earlier",
                "start": "2026-03-13T08:00:00+11:00",
                "end": "2026-03-13T09:00:00+11:00",
                "all_day": False,
            },
        ]
        result = format_event_list(events)
        lines = result.split("\n")
        assert "Earlier" in lines[0]
        assert "Later" in lines[1]


class TestFormatEventsGrouped:
    def test_empty(self) -> None:
        assert format_events_grouped({}) == "(no events)"

    def test_grouped_output(self) -> None:
        grouped = {
            "Work": [
                {
                    "uid": "w1",
                    "title": "Standup",
                    "start": "2026-03-13T09:00:00+11:00",
                    "end": "2026-03-13T09:30:00+11:00",
                    "all_day": False,
                },
            ],
            "Personal": [],
        }
        result = format_events_grouped(grouped)
        assert "## Work (1 events)" in result
        assert "Standup" in result
        assert "## Personal (0 events)" in result
        assert "(no events)" in result

    def test_error_calendar(self) -> None:
        grouped = {"Broken": [{"error": "Connection refused"}]}
        result = format_events_grouped(grouped)
        assert "Error: Connection refused" in result


class TestFormatEventDetail:
    def test_full_detail(self) -> None:
        event = {
            "uid": "d1",
            "title": "Board meeting",
            "start": "2026-03-13T10:00:00+11:00",
            "end": "2026-03-13T12:00:00+11:00",
            "all_day": False,
            "location": "Conference room",
            "description": "Quarterly review",
            "attendees": [{"name": "CEO", "email": "ceo@co.com", "status": "ACCEPTED"}],
            "recurrence_rule": "FREQ=MONTHLY",
            "sequence": 2,
        }
        result = format_event_detail(event)
        assert "Title: Board meeting" in result
        assert "Location: Conference room" in result
        assert "Description: Quarterly review" in result
        assert "Attendee: CEO <ceo@co.com> (ACCEPTED)" in result
        assert "Recurrence: FREQ=MONTHLY" in result
        assert "UID: d1" in result
        assert "Sequence: 2" in result


class TestFormatCalendarList:
    def test_empty(self) -> None:
        assert format_calendar_list([]) == "(no calendars)"

    def test_with_provider(self) -> None:
        cals = [
            {"name": "Work", "uid": "work-123", "provider": "fastmail"},
            {"name": "Personal", "uid": "pers-456", "provider": "default"},
        ]
        result = format_calendar_list(cals)
        assert "Work | provider=fastmail | uid=work-123" in result
        # "default" provider should not show provider=
        assert "Personal | uid=pers-456" in result
        assert "provider=default" not in result


class TestFormatInfo:
    def test_connected(self) -> None:
        info = {
            "providers": [{"name": "fastmail", "status": "connected", "calendars": 5}],
            "total_calendars": 5,
            "write_enabled": False,
        }
        result = format_info(info)
        assert "fastmail: connected (5 calendars)" in result
        assert "Write enabled: False" in result

    def test_error_provider(self) -> None:
        info = {
            "providers": [{"name": "icloud", "status": "error", "error": "auth failed"}],
            "total_calendars": 0,
            "write_enabled": False,
        }
        result = format_info(info)
        assert "icloud: error — auth failed" in result


class TestFormatFreebusy:
    def test_empty(self) -> None:
        assert "completely free" in format_freebusy([])

    def test_busy_periods(self) -> None:
        periods = [
            {"start": "2026-03-13T09:00:00+11:00", "end": "2026-03-13T10:00:00+11:00"},
            {"start": "2026-03-13T14:00:00+11:00", "end": "2026-03-13T15:30:00+11:00"},
        ]
        result = format_freebusy(periods)
        assert "BUSY 09:00-10:00" in result
        assert "BUSY 14:00-15:30" in result
