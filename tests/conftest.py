"""Shared test fixtures for CalDAV Blade MCP tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest

from caldav_blade_mcp.models import ProviderConfig


@pytest.fixture
def provider_config() -> ProviderConfig:
    """A single test provider config."""
    return ProviderConfig(
        name="test",
        url="https://caldav.example.com/dav/calendars/user/test@example.com/",
        username="test@example.com",
        password="test-password",
    )


def make_vevent(
    uid: str = "test-uid-123",
    summary: str = "Test Event",
    dtstart: datetime | None = None,
    dtend: datetime | None = None,
    description: str | None = None,
    location: str | None = None,
    all_day: bool = False,
    attendees: list[dict[str, str]] | None = None,
    rrule: str | None = None,
    sequence: int = 0,
) -> MagicMock:
    """Create a mock VEVENT component."""
    if dtstart is None:
        dtstart = datetime(2026, 3, 13, 9, 0, tzinfo=UTC)
    if dtend is None:
        dtend = dtstart + timedelta(hours=1)

    vevent = MagicMock()
    vevent.name = "VEVENT"
    vevent.subcomponents = []

    # Build a dict-like get() for properties
    props: dict[str, Any] = {
        "UID": uid,
        "SUMMARY": summary,
        "SEQUENCE": sequence,
    }
    if description:
        props["DESCRIPTION"] = description
    if location:
        props["LOCATION"] = location

    # DTSTART
    dtstart_mock = MagicMock()
    if all_day:
        dtstart_mock.dt = dtstart.date()
    else:
        dtstart_mock.dt = dtstart
    props["DTSTART"] = dtstart_mock

    # DTEND
    dtend_mock = MagicMock()
    if all_day:
        dtend_mock.dt = dtend.date()
    else:
        dtend_mock.dt = dtend
    props["DTEND"] = dtend_mock

    if rrule:
        rrule_mock = MagicMock()
        rrule_mock.to_ical.return_value = rrule.encode()
        props["RRULE"] = rrule_mock

    if attendees:
        att_list = []
        for att in attendees:
            att_mock = MagicMock()
            att_mock.__str__ = lambda self, email=att["email"]: f"mailto:{email}"
            att_mock.params = {"CN": att.get("name", att["email"]), "PARTSTAT": att.get("status", "NEEDS-ACTION")}
            att_list.append(att_mock)
        props["ATTENDEE"] = att_list

    def vevent_get(key: str, default: Any = None) -> Any:
        return props.get(key, default)

    vevent.get = vevent_get
    # MagicMock dunder methods receive self as first arg
    vevent.__contains__ = lambda self, key: key in props
    vevent.__getitem__ = lambda self, key: props[key]

    return vevent


def make_calendar_obj(name: str = "Test Calendar", cal_id: str = "test-cal-id") -> MagicMock:
    """Create a mock calendar object."""
    cal = MagicMock()
    cal.name = name
    cal.id = cal_id
    cal.url = f"https://caldav.example.com/dav/calendars/user/test@example.com/{cal_id}/"
    return cal


def make_event_obj(vevent: MagicMock | None = None) -> MagicMock:
    """Create a mock caldav event object wrapping a VEVENT."""
    if vevent is None:
        vevent = make_vevent()
    event = MagicMock()
    ical = MagicMock()
    ical.subcomponents = [vevent]
    event.icalendar_instance = ical
    return event
