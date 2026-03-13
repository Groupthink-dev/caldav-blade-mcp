"""Tests for MCP server tools."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from caldav_blade_mcp.server import (
    cal_calendars,
    cal_create,
    cal_delete,
    cal_event,
    cal_events,
    cal_events_batch,
    cal_freebusy,
    cal_info,
    cal_move,
    cal_search,
    cal_today,
    cal_update,
    cal_week,
)


@pytest.fixture
def mock_client() -> MagicMock:
    client = MagicMock()
    with patch("caldav_blade_mcp.server._get_client", return_value=client):
        yield client


class TestReadTools:
    async def test_cal_info(self, mock_client: MagicMock) -> None:
        mock_client.info.return_value = {
            "providers": [{"name": "fastmail", "status": "connected", "calendars": 5}],
            "total_calendars": 5,
            "write_enabled": False,
        }
        result = await cal_info()
        assert "fastmail: connected" in result
        assert "5 calendars" in result

    async def test_cal_calendars(self, mock_client: MagicMock) -> None:
        mock_client.list_calendars.return_value = [
            {"name": "Work", "uid": "work-123", "provider": "fastmail"},
        ]
        result = await cal_calendars()
        assert "Work" in result
        assert "uid=work-123" in result

    async def test_cal_events(self, mock_client: MagicMock) -> None:
        mock_client.get_events.return_value = [
            {
                "uid": "ev-1",
                "title": "Standup",
                "start": "2026-03-13T09:00:00+11:00",
                "end": "2026-03-13T09:30:00+11:00",
                "all_day": False,
            },
        ]
        result = await cal_events("Work", "2026-03-13T00:00:00+11:00", "2026-03-14T00:00:00+11:00")
        assert "Standup" in result
        assert "09:00-09:30" in result

    async def test_cal_events_batch(self, mock_client: MagicMock) -> None:
        mock_client.get_events_batch.return_value = {
            "Work": [
                {
                    "uid": "ev-1",
                    "title": "Meeting",
                    "start": "2026-03-13T10:00:00+11:00",
                    "end": "2026-03-13T11:00:00+11:00",
                    "all_day": False,
                },
            ],
            "Personal": [],
        }
        result = await cal_events_batch(["Work", "Personal"], "2026-03-13T00:00:00+11:00", "2026-03-14T00:00:00+11:00")
        assert "## Work" in result
        assert "Meeting" in result
        assert "## Personal" in result

    async def test_cal_event(self, mock_client: MagicMock) -> None:
        mock_client.get_event.return_value = {
            "uid": "ev-1",
            "title": "Board meeting",
            "start": "2026-03-13T10:00:00+11:00",
            "end": "2026-03-13T12:00:00+11:00",
            "all_day": False,
            "location": "HQ",
            "description": "Quarterly review",
        }
        result = await cal_event("ev-1")
        assert "Title: Board meeting" in result
        assert "Location: HQ" in result

    async def test_cal_search(self, mock_client: MagicMock) -> None:
        mock_client.search_events.return_value = [
            {
                "uid": "ev-1",
                "title": "Dentist",
                "start": "2026-03-13T14:00:00+11:00",
                "end": "2026-03-13T15:00:00+11:00",
                "all_day": False,
            },
        ]
        result = await cal_search(query="dentist")
        assert "Dentist" in result

    async def test_cal_today(self, mock_client: MagicMock) -> None:
        mock_client.get_today.return_value = {
            "Work": [
                {
                    "uid": "ev-1",
                    "title": "Standup",
                    "start": "2026-03-13T09:00:00+11:00",
                    "end": "2026-03-13T09:30:00+11:00",
                    "all_day": False,
                },
            ],
        }
        result = await cal_today()
        assert "Standup" in result

    async def test_cal_today_empty(self, mock_client: MagicMock) -> None:
        mock_client.get_today.return_value = {}
        result = await cal_today()
        assert "no events today" in result

    async def test_cal_week(self, mock_client: MagicMock) -> None:
        mock_client.get_week.return_value = {
            "Work": [
                {
                    "uid": "ev-1",
                    "title": "Sprint",
                    "start": "2026-03-13T10:00:00+11:00",
                    "end": "2026-03-13T11:00:00+11:00",
                    "all_day": False,
                },
            ],
        }
        result = await cal_week()
        assert "Sprint" in result

    async def test_cal_freebusy(self, mock_client: MagicMock) -> None:
        mock_client.freebusy.return_value = [
            {"start": "2026-03-13T09:00:00+11:00", "end": "2026-03-13T10:00:00+11:00"},
        ]
        result = await cal_freebusy("2026-03-13T00:00:00+11:00", "2026-03-14T00:00:00+11:00")
        assert "BUSY 09:00-10:00" in result

    async def test_cal_freebusy_empty(self, mock_client: MagicMock) -> None:
        mock_client.freebusy.return_value = []
        result = await cal_freebusy("2026-03-13T00:00:00+11:00", "2026-03-14T00:00:00+11:00")
        assert "completely free" in result


class TestWriteTools:
    async def test_cal_create_blocked_without_write(self, mock_client: MagicMock) -> None:
        with patch("caldav_blade_mcp.server.require_write", return_value="Error: Write operations are disabled."):
            result = await cal_create("Work", "Meeting", "2026-03-13T10:00:00+11:00", "2026-03-13T11:00:00+11:00")
            assert "Error: Write operations are disabled" in result

    async def test_cal_create_allowed(self, mock_client: MagicMock) -> None:
        mock_client.create_event.return_value = {
            "uid": "new-1",
            "title": "New meeting",
            "start": "2026-03-13T10:00:00+11:00",
            "end": "2026-03-13T11:00:00+11:00",
            "all_day": False,
        }
        with patch("caldav_blade_mcp.server.require_write", return_value=None):
            result = await cal_create("Work", "New meeting", "2026-03-13T10:00:00+11:00", "2026-03-13T11:00:00+11:00")
            assert "Created" in result
            assert "New meeting" in result

    async def test_cal_update_blocked(self, mock_client: MagicMock) -> None:
        with patch("caldav_blade_mcp.server.require_write", return_value="Error: Write operations are disabled."):
            result = await cal_update("ev-1", title="Updated")
            assert "Error: Write operations are disabled" in result

    async def test_cal_update_allowed(self, mock_client: MagicMock) -> None:
        mock_client.update_event.return_value = {
            "uid": "ev-1",
            "title": "Updated title",
            "start": "2026-03-13T10:00:00+11:00",
            "end": "2026-03-13T11:00:00+11:00",
            "all_day": False,
            "sequence": 1,
        }
        with patch("caldav_blade_mcp.server.require_write", return_value=None):
            result = await cal_update("ev-1", title="Updated title")
            assert "Updated" in result
            assert "Updated title" in result

    async def test_cal_delete_requires_confirm(self, mock_client: MagicMock) -> None:
        with patch("caldav_blade_mcp.server.require_write", return_value=None):
            result = await cal_delete("ev-1", confirm=False)
            assert "confirm=true" in result

    async def test_cal_delete_confirmed(self, mock_client: MagicMock) -> None:
        mock_client.delete_event.return_value = True
        with patch("caldav_blade_mcp.server.require_write", return_value=None):
            result = await cal_delete("ev-1", confirm=True)
            assert "Deleted event ev-1" in result

    async def test_cal_move_blocked(self, mock_client: MagicMock) -> None:
        with patch("caldav_blade_mcp.server.require_write", return_value="Error: Write operations are disabled."):
            result = await cal_move("ev-1", "Work", "Personal")
            assert "Error: Write operations are disabled" in result

    async def test_cal_move_allowed(self, mock_client: MagicMock) -> None:
        mock_client.move_event.return_value = {
            "uid": "ev-1",
            "title": "Moved event",
            "start": "2026-03-13T10:00:00+11:00",
            "end": "2026-03-13T11:00:00+11:00",
            "all_day": False,
        }
        with patch("caldav_blade_mcp.server.require_write", return_value=None):
            result = await cal_move("ev-1", "Work", "Personal")
            assert "Moved to Personal" in result
