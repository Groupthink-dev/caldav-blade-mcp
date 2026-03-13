"""Tests for CalDAV client wrapper."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from caldav_blade_mcp.client import (
    AuthError,
    CalDAVClient,
    CalDAVError,
    ConnectionError,
    NotFoundError,
    _classify_error,
    _extract_event,
    _is_all_day,
    _scrub_credentials,
)
from caldav_blade_mcp.models import ProviderConfig
from tests.conftest import make_calendar_obj, make_event_obj, make_vevent


class TestErrorClassification:
    def test_auth_errors(self) -> None:
        assert isinstance(_classify_error("unauthorized access"), AuthError)
        assert isinstance(_classify_error("Authentication failed"), AuthError)
        assert isinstance(_classify_error("Forbidden resource"), AuthError)

    def test_not_found_errors(self) -> None:
        assert isinstance(_classify_error("Calendar not found"), NotFoundError)
        assert isinstance(_classify_error("Resource does not exist"), NotFoundError)

    def test_connection_errors(self) -> None:
        assert isinstance(_classify_error("Connection refused"), ConnectionError)
        assert isinstance(_classify_error("Request timeout"), ConnectionError)

    def test_unknown_error(self) -> None:
        err = _classify_error("Something weird happened")
        assert type(err) is CalDAVError


class TestCredentialScrubbing:
    def test_scrub_url_credentials(self) -> None:
        text = "Error connecting to https://user:pass123@caldav.example.com/dav"
        result = _scrub_credentials(text)
        assert "pass123" not in result
        assert "****" in result

    def test_scrub_password_param(self) -> None:
        text = "Config: password=s3cret123 host=example.com"
        result = _scrub_credentials(text)
        assert "s3cret123" not in result
        assert "password=****" in result


class TestEventExtraction:
    def test_basic_event(self) -> None:
        vevent = make_vevent(uid="test-1", summary="Meeting", description="Important")
        result = _extract_event(vevent)
        assert result["uid"] == "test-1"
        assert result["title"] == "Meeting"
        assert result["description"] == "Important"

    def test_all_day_event(self) -> None:
        vevent = make_vevent(uid="allday-1", summary="Holiday", all_day=True)
        assert _is_all_day(vevent) is True
        result = _extract_event(vevent)
        assert result["all_day"] is True

    def test_timed_event_not_all_day(self) -> None:
        vevent = make_vevent(uid="timed-1", summary="Standup", all_day=False)
        assert _is_all_day(vevent) is False
        result = _extract_event(vevent)
        assert result["all_day"] is False

    def test_event_with_attendees(self) -> None:
        vevent = make_vevent(
            uid="att-1",
            attendees=[
                {"email": "alice@example.com", "name": "Alice", "status": "ACCEPTED"},
            ],
        )
        result = _extract_event(vevent)
        assert len(result["attendees"]) == 1
        assert result["attendees"][0]["email"] == "alice@example.com"

    def test_event_with_recurrence(self) -> None:
        vevent = make_vevent(uid="rec-1", rrule="FREQ=WEEKLY;BYDAY=MO")
        result = _extract_event(vevent)
        assert result["recurrence_rule"] == "FREQ=WEEKLY;BYDAY=MO"

    def test_null_optional_fields(self) -> None:
        vevent = make_vevent(uid="min-1")
        result = _extract_event(vevent)
        assert result["description"] is None
        assert result["location"] is None
        assert result["attendees"] is None
        assert result["recurrence_rule"] is None


class TestCalDAVClient:
    @patch("caldav_blade_mcp.client.DAVClient")
    def test_list_calendars(self, mock_dav_cls: MagicMock) -> None:
        cal1 = make_calendar_obj("Work", "work-id")
        cal2 = make_calendar_obj("Personal", "personal-id")

        mock_principal = MagicMock()
        mock_principal.calendars.return_value = [cal1, cal2]
        mock_dav_cls.return_value.principal.return_value = mock_principal

        provider = ProviderConfig(name="test", url="https://example.com", username="u", password="p")
        client = CalDAVClient(providers=[provider])
        result = client.list_calendars()

        assert len(result) == 2
        assert result[0]["name"] == "Work"
        assert result[0]["provider"] == "test"

    @patch("caldav_blade_mcp.client.DAVClient")
    def test_info(self, mock_dav_cls: MagicMock) -> None:
        cal1 = make_calendar_obj("Work", "work-id")
        mock_principal = MagicMock()
        mock_principal.calendars.return_value = [cal1]
        mock_dav_cls.return_value.principal.return_value = mock_principal

        provider = ProviderConfig(name="fastmail", url="https://example.com", username="u", password="p")
        client = CalDAVClient(providers=[provider])
        result = client.info()

        assert result["total_calendars"] == 1
        assert result["providers"][0]["name"] == "fastmail"
        assert result["providers"][0]["status"] == "connected"

    @patch("caldav_blade_mcp.client.DAVClient")
    def test_get_events(self, mock_dav_cls: MagicMock) -> None:
        vevent = make_vevent(uid="ev-1", summary="Standup")
        event_obj = make_event_obj(vevent)

        cal = make_calendar_obj("Work", "work-id")
        cal.search.return_value = [event_obj]

        mock_principal = MagicMock()
        mock_principal.calendars.return_value = [cal]
        mock_dav_cls.return_value.principal.return_value = mock_principal

        provider = ProviderConfig(name="test", url="https://example.com", username="u", password="p")
        client = CalDAVClient(providers=[provider])
        result = client.get_events("Work", "2026-03-13T00:00:00+11:00", "2026-03-14T00:00:00+11:00")

        assert len(result) == 1
        assert result[0]["uid"] == "ev-1"
        assert result[0]["title"] == "Standup"

    @patch("caldav_blade_mcp.client.DAVClient")
    def test_get_events_batch(self, mock_dav_cls: MagicMock) -> None:
        vevent1 = make_vevent(uid="ev-1", summary="Morning")
        vevent2 = make_vevent(uid="ev-2", summary="Afternoon")

        cal1 = make_calendar_obj("Work", "work-id")
        cal1.search.return_value = [make_event_obj(vevent1)]

        cal2 = make_calendar_obj("Personal", "personal-id")
        cal2.search.return_value = [make_event_obj(vevent2)]

        mock_principal = MagicMock()
        mock_principal.calendars.return_value = [cal1, cal2]
        mock_dav_cls.return_value.principal.return_value = mock_principal

        provider = ProviderConfig(name="test", url="https://example.com", username="u", password="p")
        client = CalDAVClient(providers=[provider])
        result = client.get_events_batch(["Work", "Personal"], "2026-03-13T00:00:00+11:00", "2026-03-14T00:00:00+11:00")

        assert "Work" in result
        assert "Personal" in result
        assert len(result["Work"]) == 1
        assert len(result["Personal"]) == 1

    @patch("caldav_blade_mcp.client.DAVClient")
    def test_find_calendar_not_found(self, mock_dav_cls: MagicMock) -> None:
        mock_principal = MagicMock()
        mock_principal.calendars.return_value = [make_calendar_obj("Work", "work-id")]
        mock_dav_cls.return_value.principal.return_value = mock_principal

        provider = ProviderConfig(name="test", url="https://example.com", username="u", password="p")
        client = CalDAVClient(providers=[provider])

        with pytest.raises(NotFoundError, match="Calendar not found"):
            client.get_events("NonExistent", "2026-03-13T00:00:00+11:00", "2026-03-14T00:00:00+11:00")

    @patch("caldav_blade_mcp.client.DAVClient")
    def test_search_events_by_query(self, mock_dav_cls: MagicMock) -> None:
        vevent1 = make_vevent(uid="ev-1", summary="Team standup")
        vevent2 = make_vevent(uid="ev-2", summary="Dentist appointment")

        cal = make_calendar_obj("All", "all-id")
        cal.search.return_value = [make_event_obj(vevent1), make_event_obj(vevent2)]

        mock_principal = MagicMock()
        mock_principal.calendars.return_value = [cal]
        mock_dav_cls.return_value.principal.return_value = mock_principal

        provider = ProviderConfig(name="test", url="https://example.com", username="u", password="p")
        client = CalDAVClient(providers=[provider])
        result = client.search_events(query="standup")

        assert len(result) == 1
        assert result[0]["title"] == "Team standup"

    @patch("caldav_blade_mcp.client.DAVClient")
    def test_search_events_by_location(self, mock_dav_cls: MagicMock) -> None:
        vevent1 = make_vevent(uid="ev-1", summary="Meeting", location="Room A")
        vevent2 = make_vevent(uid="ev-2", summary="Lunch")

        cal = make_calendar_obj("All", "all-id")
        cal.search.return_value = [make_event_obj(vevent1), make_event_obj(vevent2)]

        mock_principal = MagicMock()
        mock_principal.calendars.return_value = [cal]
        mock_dav_cls.return_value.principal.return_value = mock_principal

        provider = ProviderConfig(name="test", url="https://example.com", username="u", password="p")
        client = CalDAVClient(providers=[provider])
        result = client.search_events(location="Room A")

        assert len(result) == 1
        assert result[0]["title"] == "Meeting"


class TestMultiProvider:
    @patch("caldav_blade_mcp.client.DAVClient")
    def test_calendars_from_multiple_providers(self, mock_dav_cls: MagicMock) -> None:
        cal1 = make_calendar_obj("Fastmail Work", "fm-work")
        cal2 = make_calendar_obj("iCloud Family", "ic-family")

        principal1 = MagicMock()
        principal1.calendars.return_value = [cal1]
        principal2 = MagicMock()
        principal2.calendars.return_value = [cal2]

        mock_dav_cls.return_value.principal.side_effect = [principal1, principal2]

        providers = [
            ProviderConfig(name="fastmail", url="https://fm.example.com", username="u1", password="p1"),
            ProviderConfig(name="icloud", url="https://ic.example.com", username="u2", password="p2"),
        ]
        client = CalDAVClient(providers=providers)
        result = client.list_calendars()

        assert len(result) == 2
        assert result[0]["provider"] == "fastmail"
        assert result[1]["provider"] == "icloud"
