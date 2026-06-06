"""Tests for CalDAV client wrapper."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import caldav.lib.error
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
    _parse_dt,
    _scrub_credentials,
)
from caldav_blade_mcp.models import ProviderConfig
from tests.conftest import make_calendar_obj, make_event_obj, make_searchable_calendar, make_vevent


def _client_with_calendars(mock_dav_cls: MagicMock, calendars: list[MagicMock]) -> CalDAVClient:
    """Build a single-provider CalDAVClient whose principal returns ``calendars``."""
    mock_principal = MagicMock()
    mock_principal.calendars.return_value = calendars
    mock_dav_cls.return_value.principal.return_value = mock_principal
    provider = ProviderConfig(name="test", url="https://example.com", username="u", password="p")
    return CalDAVClient(providers=[provider])


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

    @patch("caldav_blade_mcp.client.DAVClient")
    def test_search_dateless_does_not_expand(self, mock_dav_cls: MagicMock) -> None:
        # D3: a dateless search MUST NOT pass expand=True (iCloud/caldav reject
        # "expand without a date range" and the broad except returns a false-empty).
        cal = make_calendar_obj("All", "all-id")
        cal.search.return_value = []
        mock_principal = MagicMock()
        mock_principal.calendars.return_value = [cal]
        mock_dav_cls.return_value.principal.return_value = mock_principal

        provider = ProviderConfig(name="test", url="https://example.com", username="u", password="p")
        client = CalDAVClient(providers=[provider])

        client.search_events(query="standup")
        assert cal.search.call_args.kwargs["expand"] is False

        cal.search.reset_mock()
        client.search_events(query="standup", start="2026-06-01T00:00:00+10:00", end="2026-06-30T00:00:00+10:00")
        assert cal.search.call_args.kwargs["expand"] is True

    @patch("caldav_blade_mcp.client.DAVClient")
    def test_connect_auth_failure_rolls_back_and_retries(self, mock_dav_cls: MagicMock) -> None:
        # D5: an auth failure during connect() must NOT cache a half-initialised
        # connection. The real error must propagate (not a derived AttributeError),
        # and a subsequent connect() must re-attempt rather than return a cached None.
        class _BoomError(Exception):
            pass

        good_principal = MagicMock()
        mock_dav_cls.return_value.principal.side_effect = [_BoomError("401 Unauthorized"), good_principal]

        provider = ProviderConfig(name="test", url="https://example.com", username="u", password="p")
        client = CalDAVClient(providers=[provider])
        conn = client._providers["test"]

        with pytest.raises(_BoomError):
            conn.connect()
        assert conn._dav is None
        assert conn._principal is None

        conn.connect()  # re-attempts; does not return a cached None
        assert conn._principal is good_principal
        assert mock_dav_cls.return_value.principal.call_count == 2


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


class TestParseDt:
    """D1 — date-only ISO → date (VALUE=DATE); timed ISO → datetime."""

    def test_date_only_autodetect(self) -> None:
        result = _parse_dt("2026-06-10")
        assert isinstance(result, date)
        assert not isinstance(result, datetime)
        assert result == date(2026, 6, 10)

    def test_timed_autodetect(self) -> None:
        result = _parse_dt("2026-06-10T09:00:00+10:00")
        assert isinstance(result, datetime)

    def test_all_day_true_truncates_datetime(self) -> None:
        result = _parse_dt("2026-06-10T09:00:00+10:00", all_day=True)
        assert isinstance(result, date)
        assert not isinstance(result, datetime)
        assert result == date(2026, 6, 10)

    def test_all_day_false_forces_datetime(self) -> None:
        # A date-only string with all_day=False still parses as a datetime.
        result = _parse_dt("2026-06-10", all_day=False)
        assert isinstance(result, datetime)


class TestAllDayConstruction:
    """D1 — create/update must produce date instances the read path detects."""

    @patch("caldav_blade_mcp.client.DAVClient")
    def test_create_all_day_passes_date(self, mock_dav_cls: MagicMock) -> None:
        cal = make_searchable_calendar("Work", "work-id")
        cal.save_event.return_value = make_event_obj(make_vevent(uid="ev-1"))
        client = _client_with_calendars(mock_dav_cls, [cal])

        client.create_event("Work", "Holiday", start="2026-06-10", end="2026-06-11")

        kwargs = cal.save_event.call_args.kwargs
        assert isinstance(kwargs["dtstart"], date)
        assert not isinstance(kwargs["dtstart"], datetime)
        assert isinstance(kwargs["dtend"], date)
        assert not isinstance(kwargs["dtend"], datetime)

    @patch("caldav_blade_mcp.client.DAVClient")
    def test_create_all_day_true_truncates(self, mock_dav_cls: MagicMock) -> None:
        cal = make_searchable_calendar("Work", "work-id")
        cal.save_event.return_value = make_event_obj(make_vevent(uid="ev-1"))
        client = _client_with_calendars(mock_dav_cls, [cal])

        client.create_event(
            "Work", "Holiday", start="2026-06-10T09:00:00+10:00", end="2026-06-10T17:00:00+10:00", all_day=True
        )

        kwargs = cal.save_event.call_args.kwargs
        assert isinstance(kwargs["dtstart"], date)
        assert not isinstance(kwargs["dtstart"], datetime)

    @patch("caldav_blade_mcp.client.DAVClient")
    def test_create_timed_stays_datetime(self, mock_dav_cls: MagicMock) -> None:
        cal = make_searchable_calendar("Work", "work-id")
        cal.save_event.return_value = make_event_obj(make_vevent(uid="ev-1"))
        client = _client_with_calendars(mock_dav_cls, [cal])

        client.create_event(
            "Work", "Standup", start="2026-06-10T09:00:00+10:00", end="2026-06-10T09:30:00+10:00"
        )

        kwargs = cal.save_event.call_args.kwargs
        assert isinstance(kwargs["dtstart"], datetime)

    @patch("caldav_blade_mcp.client.DAVClient")
    def test_update_all_day_adds_date(self, mock_dav_cls: MagicMock) -> None:
        vevent = make_vevent(uid="ev-1")
        event_obj = make_event_obj(vevent)
        cal = make_searchable_calendar("Work", "work-id", events=[event_obj])
        client = _client_with_calendars(mock_dav_cls, [cal])

        # Capture what comp.add("DTSTART", ...) receives.
        added: dict[str, Any] = {}
        original_add = vevent.add

        def capture_add(key: str, value: Any) -> Any:
            added[key] = value
            return original_add(key, value)

        vevent.add = capture_add

        client.update_event("ev-1", calendar="Work", start="2026-06-10")

        assert "DTSTART" in added
        assert isinstance(added["DTSTART"], date)
        assert not isinstance(added["DTSTART"], datetime)


class TestFindEventViaSearch:
    """D2/D4 — UID lookup uses cal.search, never object_by_uid."""

    @patch("caldav_blade_mcp.client.DAVClient")
    def test_uid_lookup_uses_search(self, mock_dav_cls: MagicMock) -> None:
        target = make_event_obj(make_vevent(uid="ev-1"))
        cal = make_searchable_calendar("Work", "work-id", events=[target])
        client = _client_with_calendars(mock_dav_cls, [cal])

        _, resolved_cal, resolved_event = client._find_event("ev-1", "Work")

        assert resolved_event is target
        cal.search.assert_called_once()
        # No expand on the UID lookup (D3-class 412 avoidance).
        assert cal.search.call_args.kwargs.get("expand") is None
        cal.object_by_uid.assert_not_called()

    @patch("caldav_blade_mcp.client.DAVClient")
    def test_uid_no_match_raises_not_found(self, mock_dav_cls: MagicMock) -> None:
        other = make_event_obj(make_vevent(uid="ev-other"))
        cal = make_searchable_calendar("Work", "work-id", events=[other])
        client = _client_with_calendars(mock_dav_cls, [cal])

        with pytest.raises(NotFoundError, match="not found in calendar"):
            client._find_event("ev-1", "Work")

    @patch("caldav_blade_mcp.client.DAVClient")
    def test_search_report_error_maps_to_caldav_error(self, mock_dav_cls: MagicMock) -> None:
        cal = make_searchable_calendar("Work", "work-id")
        cal.search.side_effect = caldav.lib.error.ReportError("412 Precondition Failed")
        client = _client_with_calendars(mock_dav_cls, [cal])

        with pytest.raises(CalDAVError):
            client._find_event("ev-1", "Work")


class TestNonFatalRefetch:
    """D2 — a post-write re-fetch failure must not turn a landed PUT into a failure."""

    @patch("caldav_blade_mcp.client.DAVClient")
    def test_create_refetch_failure_returns_best_effort(self, mock_dav_cls: MagicMock) -> None:
        cal = make_searchable_calendar("Work", "work-id")
        cal.save_event.return_value = make_event_obj(make_vevent(uid="ev-1"))
        # Re-fetch search blows up after the PUT landed.
        cal.search.side_effect = caldav.lib.error.ReportError("412 Precondition Failed")
        client = _client_with_calendars(mock_dav_cls, [cal])

        result = client.create_event(
            "Work", "Standup", start="2026-06-10T09:00:00+10:00", end="2026-06-10T09:30:00+10:00"
        )

        assert result["uid"] == "ev-1"
        assert result["title"] == "Standup"

    @patch("caldav_blade_mcp.client.DAVClient")
    def test_update_refetch_failure_returns_best_effort(self, mock_dav_cls: MagicMock) -> None:
        vevent = make_vevent(uid="ev-1")
        event_obj = make_event_obj(vevent)
        # First search resolves the event for the edit; second (re-fetch) fails.
        cal = make_searchable_calendar("Work", "work-id")
        cal.search.side_effect = [[event_obj], caldav.lib.error.ReportError("412 Precondition Failed")]
        client = _client_with_calendars(mock_dav_cls, [cal])

        result = client.update_event("ev-1", calendar="Work", title="Renamed")

        assert result["uid"] == "ev-1"
        assert result["title"] == "Renamed"


class TestWriteMethodClassification:
    """D6 — caldav DAVError from a write sink surfaces as blade CalDAVError."""

    @patch("caldav_blade_mcp.client.DAVClient")
    def test_create_put_error_classified(self, mock_dav_cls: MagicMock) -> None:
        cal = make_searchable_calendar("Work", "work-id")
        cal.save_event.side_effect = caldav.lib.error.PutError("PUT failed")
        client = _client_with_calendars(mock_dav_cls, [cal])

        with pytest.raises(CalDAVError):
            client.create_event(
                "Work", "Standup", start="2026-06-10T09:00:00+10:00", end="2026-06-10T09:30:00+10:00"
            )

    @patch("caldav_blade_mcp.client.DAVClient")
    def test_delete_error_classified(self, mock_dav_cls: MagicMock) -> None:
        event_obj = make_event_obj(make_vevent(uid="ev-1"))
        event_obj.delete.side_effect = caldav.lib.error.DeleteError("DELETE failed")
        cal = make_searchable_calendar("Work", "work-id", events=[event_obj])
        client = _client_with_calendars(mock_dav_cls, [cal])

        with pytest.raises(CalDAVError):
            client.delete_event("ev-1", "Work")


class TestExtractAlarm:
    """D8 — _extract_event surfaces alarm_minutes from a VALARM TRIGGER."""

    def test_alarm_extracted(self) -> None:
        vevent = make_vevent(uid="ev-1")
        valarm = MagicMock()
        valarm.name = "VALARM"
        trigger = MagicMock()
        trigger.dt = timedelta(minutes=-15)
        valarm.get = lambda key, default=None: {"TRIGGER": trigger}.get(key, default)
        vevent.subcomponents = [valarm]

        result = _extract_event(vevent)
        assert result["alarm_minutes"] == 15

    def test_no_alarm_is_none(self) -> None:
        vevent = make_vevent(uid="ev-1")  # subcomponents = []
        result = _extract_event(vevent)
        assert result["alarm_minutes"] is None


class TestMoveEvent:
    """D8/D9/D10 — create-first / verify / delete-last with fidelity + no data loss."""

    def _build_move_client(
        self, mock_dav_cls: MagicMock, source_vevent: MagicMock
    ) -> tuple[CalDAVClient, MagicMock, MagicMock, MagicMock]:
        """Wire a from-cal (holds source) and to-cal (create target + verify)."""
        source_obj = make_event_obj(source_vevent)
        from_cal = make_searchable_calendar("Work", "work-id", events=[source_obj])

        created_vevent = make_vevent(uid="ev-1")
        created_obj = make_event_obj(created_vevent)
        to_cal = make_searchable_calendar("Personal", "personal-id")
        to_cal.save_event.return_value = make_event_obj(make_vevent(uid="ev-1"))
        # verify search on the destination returns the created event.
        to_cal.search.return_value = [created_obj]

        client = _client_with_calendars(mock_dav_cls, [from_cal, to_cal])
        return client, from_cal, to_cal, source_obj

    @patch("caldav_blade_mcp.client.DAVClient")
    def test_move_threads_attendees_and_alarm(self, mock_dav_cls: MagicMock) -> None:
        source_vevent = make_vevent(
            uid="ev-1",
            attendees=[{"email": "alice@example.com", "name": "Alice", "status": "ACCEPTED"}],
        )
        valarm = MagicMock()
        valarm.name = "VALARM"
        trigger = MagicMock()
        trigger.dt = timedelta(minutes=-30)
        valarm.get = lambda key, default=None: {"TRIGGER": trigger}.get(key, default)
        source_vevent.subcomponents = [valarm]

        client, from_cal, to_cal, _ = self._build_move_client(mock_dav_cls, source_vevent)
        with patch.object(client, "create_event", wraps=client.create_event) as spy:
            client.move_event("ev-1", "Work", "Personal")

        ckwargs = spy.call_args.kwargs
        assert ckwargs["attendees"] is not None
        assert ckwargs["attendees"][0]["email"] == "alice@example.com"
        assert ckwargs["alarm_minutes"] == 30

    @patch("caldav_blade_mcp.client.DAVClient")
    def test_move_create_failure_never_deletes(self, mock_dav_cls: MagicMock) -> None:
        source_vevent = make_vevent(uid="ev-1")
        client, from_cal, to_cal, source_obj = self._build_move_client(mock_dav_cls, source_vevent)
        # Destination create raises — source must survive.
        to_cal.save_event.side_effect = caldav.lib.error.PutError("PUT failed")

        with pytest.raises(CalDAVError):
            client.move_event("ev-1", "Work", "Personal")

        source_obj.delete.assert_not_called()

    @patch("caldav_blade_mcp.client.DAVClient")
    def test_move_partial_success_on_delete_failure(self, mock_dav_cls: MagicMock) -> None:
        source_vevent = make_vevent(uid="ev-1")
        client, from_cal, to_cal, source_obj = self._build_move_client(mock_dav_cls, source_vevent)
        # Create + verify succeed; source delete fails after the irreversible copy.
        source_obj.delete.side_effect = caldav.lib.error.DeleteError("DELETE failed")

        result = client.move_event("ev-1", "Work", "Personal")

        assert "but source not removed" in result.get("move_status", "")

    @patch("caldav_blade_mcp.client.DAVClient")
    def test_move_all_day_preserved(self, mock_dav_cls: MagicMock) -> None:
        source_vevent = make_vevent(uid="ev-1", all_day=True)
        client, from_cal, to_cal, _ = self._build_move_client(mock_dav_cls, source_vevent)
        with patch.object(client, "create_event", wraps=client.create_event) as spy:
            client.move_event("ev-1", "Work", "Personal")

        ckwargs = spy.call_args.kwargs
        assert ckwargs["all_day"] is True
        # The all-day source serialises to a date-only ISO string; create re-parses to a date.
        assert "T" not in ckwargs["start"]
        save_kwargs = to_cal.save_event.call_args.kwargs
        assert isinstance(save_kwargs["dtstart"], date)
        assert not isinstance(save_kwargs["dtstart"], datetime)
