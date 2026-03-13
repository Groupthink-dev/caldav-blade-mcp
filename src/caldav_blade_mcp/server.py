"""CalDAV Blade MCP Server — calendar events, scheduling, and availability.

Wraps the CalDAV protocol via the ``caldav`` library as MCP tools. Token-efficient
by default: compact output, null-field omission, batch operations.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import Field

from caldav_blade_mcp.client import CalDAVClient, CalDAVError
from caldav_blade_mcp.formatters import (
    format_calendar_list,
    format_event_detail,
    format_event_list,
    format_events_grouped,
    format_freebusy,
    format_info,
)
from caldav_blade_mcp.models import (
    MAX_DESCRIPTION_LENGTH,
    MAX_TITLE_LENGTH,
    require_write,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Transport configuration
# ---------------------------------------------------------------------------

TRANSPORT = os.environ.get("CALDAV_MCP_TRANSPORT", "stdio")
HTTP_HOST = os.environ.get("CALDAV_MCP_HOST", "127.0.0.1")
HTTP_PORT = int(os.environ.get("CALDAV_MCP_PORT", "8766"))

# ---------------------------------------------------------------------------
# FastMCP server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "CalDAVBlade",
    instructions=(
        "CalDAV calendar operations. Read events, search, check availability. "
        "Batch operations for token efficiency. "
        "Write operations require CALDAV_WRITE_ENABLED=true."
    ),
)

# Lazy-initialized client
_client: CalDAVClient | None = None


def _get_client() -> CalDAVClient:
    """Get or create the CalDAVClient singleton."""
    global _client  # noqa: PLW0603
    if _client is None:
        _client = CalDAVClient()
    return _client


def _error_response(e: CalDAVError) -> str:
    """Format a client error as a user-friendly string."""
    return f"Error: {e}"


async def _run(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Run a blocking client method in a thread to avoid blocking the event loop."""
    return await asyncio.to_thread(fn, *args, **kwargs)


# ===========================================================================
# READ TOOLS
# ===========================================================================


@mcp.tool()
async def cal_info() -> str:
    """Health check: list providers, connection status, calendar count, write gate status."""
    try:
        info = await _run(_get_client().info)
        return format_info(info)
    except CalDAVError as e:
        return _error_response(e)


@mcp.tool()
async def cal_calendars() -> str:
    """List all calendars across all providers. Returns name, UID, and provider for each."""
    try:
        calendars = await _run(_get_client().list_calendars)
        return format_calendar_list(calendars)
    except CalDAVError as e:
        return _error_response(e)


@mcp.tool()
async def cal_events(
    calendar: Annotated[str, Field(description="Calendar name or UID")],
    start: Annotated[str, Field(description="Start of range (ISO 8601, e.g. 2026-03-13T00:00:00+11:00)")],
    end: Annotated[str, Field(description="End of range (ISO 8601)")],
) -> str:
    """Get events from a single calendar in a date range. Compact one-line-per-event output."""
    try:
        events = await _run(_get_client().get_events, calendar, start, end)
        return format_event_list(events)
    except CalDAVError as e:
        return _error_response(e)


@mcp.tool()
async def cal_events_batch(
    calendars: Annotated[list[str], Field(description="List of calendar names or UIDs")],
    start: Annotated[str, Field(description="Start of range (ISO 8601)")],
    end: Annotated[str, Field(description="End of range (ISO 8601)")],
) -> str:
    """Get events from multiple calendars in one call, grouped by calendar.

    Preferred for digests and multi-calendar views — replaces N individual cal_events calls.
    """
    try:
        grouped = await _run(_get_client().get_events_batch, calendars, start, end)
        return format_events_grouped(grouped)
    except CalDAVError as e:
        return _error_response(e)


@mcp.tool()
async def cal_event(
    event_uid: Annotated[str, Field(description="iCalendar UID of the event")],
    calendar: Annotated[
        str | None, Field(description="Calendar to search (optional — searches all if omitted)")
    ] = None,
) -> str:
    """Get a single event by UID with full details including attendees, recurrence, and description."""
    try:
        event = await _run(_get_client().get_event, event_uid, calendar)
        return format_event_detail(event)
    except CalDAVError as e:
        return _error_response(e)


@mcp.tool()
async def cal_search(
    query: Annotated[str | None, Field(description="Text search in title and description")] = None,
    attendee: Annotated[str | None, Field(description="Filter by attendee email")] = None,
    location: Annotated[str | None, Field(description="Filter by location (substring)")] = None,
    calendar: Annotated[str | None, Field(description="Restrict to one calendar")] = None,
    start: Annotated[str | None, Field(description="Earliest event start (ISO 8601)")] = None,
    end: Annotated[str | None, Field(description="Latest event end (ISO 8601)")] = None,
) -> str:
    """Search events by text, attendee email, or location. Optional calendar and date scope."""
    try:
        events = await _run(
            _get_client().search_events,
            query=query,
            attendee=attendee,
            location=location,
            calendar=calendar,
            start=start,
            end=end,
        )
        return format_event_list(events)
    except CalDAVError as e:
        return _error_response(e)


@mcp.tool()
async def cal_today() -> str:
    """Today's events across all calendars, grouped by calendar. Compact output."""
    try:
        grouped = await _run(_get_client().get_today)
        if not grouped:
            return "(no events today)"
        return format_events_grouped(grouped)
    except CalDAVError as e:
        return _error_response(e)


@mcp.tool()
async def cal_week(
    start_monday: Annotated[bool, Field(description="If true, week starts Monday; otherwise starts today")] = True,
) -> str:
    """This week's events across all calendars, grouped by calendar. Compact output."""
    try:
        grouped = await _run(_get_client().get_week, start_monday)
        if not grouped:
            return "(no events this week)"
        return format_events_grouped(grouped)
    except CalDAVError as e:
        return _error_response(e)


@mcp.tool()
async def cal_freebusy(
    start: Annotated[str, Field(description="Start of range (ISO 8601)")],
    end: Annotated[str, Field(description="End of range (ISO 8601)")],
    calendar: Annotated[str | None, Field(description="Restrict to one calendar")] = None,
) -> str:
    """Free/busy query for a date range. Returns only busy periods — very token-efficient for availability checks."""
    try:
        periods = await _run(_get_client().freebusy, start, end, calendar)
        return format_freebusy(periods)
    except CalDAVError as e:
        return _error_response(e)


# ===========================================================================
# WRITE TOOLS (gated by CALDAV_WRITE_ENABLED=true)
# ===========================================================================


@mcp.tool()
async def cal_create(
    calendar: Annotated[str, Field(description="Calendar name or UID")],
    title: Annotated[str, Field(description="Event title", max_length=MAX_TITLE_LENGTH)],
    start: Annotated[str, Field(description="Start datetime (ISO 8601)")],
    end: Annotated[str, Field(description="End datetime (ISO 8601)")],
    description: Annotated[
        str | None, Field(description="Event description", max_length=MAX_DESCRIPTION_LENGTH)
    ] = None,
    location: Annotated[str | None, Field(description="Event location")] = None,
    recurrence_rule: Annotated[str | None, Field(description="RRULE string, e.g. FREQ=WEEKLY;BYDAY=MO")] = None,
    attendees: Annotated[
        list[dict[str, str]] | None,
        Field(description="List of {email, name?, status?} dicts"),
    ] = None,
    alarm_minutes: Annotated[int | None, Field(description="Minutes before event for reminder")] = None,
) -> str:
    """Create a new calendar event. Requires CALDAV_WRITE_ENABLED=true."""
    gate = require_write()
    if gate:
        return gate
    try:
        event = await _run(
            _get_client().create_event,
            calendar=calendar,
            title=title,
            start=start,
            end=end,
            description=description,
            location=location,
            recurrence_rule=recurrence_rule,
            attendees=attendees,
            alarm_minutes=alarm_minutes,
        )
        return f"Created: {format_event_detail(event)}"
    except CalDAVError as e:
        return _error_response(e)


@mcp.tool()
async def cal_update(
    event_uid: Annotated[str, Field(description="UID of the event to update")],
    calendar: Annotated[str | None, Field(description="Calendar to search (optional)")] = None,
    title: Annotated[str | None, Field(description="New title", max_length=MAX_TITLE_LENGTH)] = None,
    start: Annotated[str | None, Field(description="New start datetime (ISO 8601)")] = None,
    end: Annotated[str | None, Field(description="New end datetime (ISO 8601)")] = None,
    description: Annotated[str | None, Field(description="New description (empty string to clear)")] = None,
    location: Annotated[str | None, Field(description="New location (empty string to clear)")] = None,
    recurrence_rule: Annotated[str | None, Field(description="New RRULE (empty string to clear)")] = None,
) -> str:
    """Partial update of an event by UID. Only changed fields sent. Auto-increments SEQUENCE.

    Requires CALDAV_WRITE_ENABLED=true.
    """
    gate = require_write()
    if gate:
        return gate
    try:
        event = await _run(
            _get_client().update_event,
            event_uid=event_uid,
            calendar=calendar,
            title=title,
            start=start,
            end=end,
            description=description,
            location=location,
            recurrence_rule=recurrence_rule,
        )
        return f"Updated: {format_event_detail(event)}"
    except CalDAVError as e:
        return _error_response(e)


@mcp.tool()
async def cal_delete(
    event_uid: Annotated[str, Field(description="UID of the event to delete")],
    calendar: Annotated[str | None, Field(description="Calendar to search (optional)")] = None,
    confirm: Annotated[bool, Field(description="Must be true to confirm deletion")] = False,
) -> str:
    """Delete an event by UID. Requires confirm=true and CALDAV_WRITE_ENABLED=true."""
    gate = require_write()
    if gate:
        return gate
    if not confirm:
        return "Error: Set confirm=true to confirm deletion. This action cannot be undone."
    try:
        await _run(_get_client().delete_event, event_uid, calendar)
        return f"Deleted event {event_uid}"
    except CalDAVError as e:
        return _error_response(e)


@mcp.tool()
async def cal_move(
    event_uid: Annotated[str, Field(description="UID of the event to move")],
    from_calendar: Annotated[str, Field(description="Source calendar name or UID")],
    to_calendar: Annotated[str, Field(description="Destination calendar name or UID")],
) -> str:
    """Move an event between calendars. Requires CALDAV_WRITE_ENABLED=true."""
    gate = require_write()
    if gate:
        return gate
    try:
        event = await _run(
            _get_client().move_event,
            event_uid=event_uid,
            from_calendar=from_calendar,
            to_calendar=to_calendar,
        )
        return f"Moved to {to_calendar}: {format_event_detail(event)}"
    except CalDAVError as e:
        return _error_response(e)


# ===========================================================================
# Entry point
# ===========================================================================


def main() -> None:
    """Run the MCP server."""
    if TRANSPORT == "http":
        from caldav_blade_mcp.auth import BearerAuthMiddleware

        mcp.settings.http_app_kwargs = {"middleware": [BearerAuthMiddleware]}
        mcp.run(transport="streamable-http", host=HTTP_HOST, port=HTTP_PORT)
    else:
        mcp.run(transport="stdio")
