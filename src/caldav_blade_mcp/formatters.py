"""Token-efficient output formatters for CalDAV Blade MCP server.

All formatters return compact strings optimised for LLM consumption:
- One line per event
- Pipe-delimited fields
- Null-field omission
- Times in HH:MM 24h format
"""

from __future__ import annotations

from typing import Any

from dateutil.parser import isoparse


def _format_time(iso_str: str | None, all_day: bool = False) -> str:
    """Format an ISO datetime string to compact HH:MM."""
    if all_day:
        return "All day"
    if not iso_str:
        return "?"
    try:
        dt = isoparse(iso_str)
        return dt.strftime("%H:%M")
    except (ValueError, TypeError):
        return iso_str


def _format_time_range(event: dict[str, Any]) -> str:
    """Format start-end as compact time range."""
    all_day = event.get("all_day", False)
    if all_day:
        return "All day"
    start = _format_time(event.get("start"), all_day)
    end = _format_time(event.get("end"), all_day)
    return f"{start}-{end}"


def format_event_line(event: dict[str, Any]) -> str:
    """Format a single event as a compact one-line string."""
    parts = [_format_time_range(event), event.get("title", "(untitled)")]

    location = event.get("location")
    if location:
        parts.append(f"location={location}")

    attendees = event.get("attendees")
    if attendees:
        names = [a.get("name", a.get("email", "")) for a in attendees]
        parts.append(f"attendees={','.join(names)}")

    rrule = event.get("recurrence_rule")
    if rrule:
        parts.append(f"recurs={rrule}")

    uid = event.get("uid")
    if uid:
        parts.append(f"uid={uid}")

    return " | ".join(parts)


def format_event_list(events: list[dict[str, Any]]) -> str:
    """Format a list of events as compact lines."""
    if not events:
        return "(no events)"
    # Sort by start time
    events = sorted(events, key=lambda e: e.get("start") or "")
    return "\n".join(format_event_line(e) for e in events)


def format_events_grouped(grouped: dict[str, list[dict[str, Any]]]) -> str:
    """Format events grouped by calendar name."""
    if not grouped:
        return "(no events)"
    lines = []
    for cal_name, events in grouped.items():
        lines.append(f"## {cal_name} ({len(events)} events)")
        if not events:
            lines.append("(no events)")
        elif len(events) == 1 and "error" in events[0]:
            lines.append(f"Error: {events[0]['error']}")
        else:
            events_sorted = sorted(events, key=lambda e: e.get("start") or "")
            for ev in events_sorted:
                lines.append(format_event_line(ev))
        lines.append("")
    return "\n".join(lines).rstrip()


def format_event_detail(event: dict[str, Any]) -> str:
    """Format a single event with full details."""
    lines = []
    lines.append(f"Title: {event.get('title', '(untitled)')}")
    lines.append(f"Time: {_format_time_range(event)}")

    start = event.get("start")
    end = event.get("end")
    if start:
        lines.append(f"Start: {start}")
    if end:
        lines.append(f"End: {end}")

    location = event.get("location")
    if location:
        lines.append(f"Location: {location}")

    description = event.get("description")
    if description:
        lines.append(f"Description: {description}")

    attendees = event.get("attendees")
    if attendees:
        for a in attendees:
            lines.append(f"Attendee: {a.get('name', '')} <{a.get('email', '')}> ({a.get('status', '')})")

    rrule = event.get("recurrence_rule")
    if rrule:
        lines.append(f"Recurrence: {rrule}")

    uid = event.get("uid")
    if uid:
        lines.append(f"UID: {uid}")

    seq = event.get("sequence")
    if seq:
        lines.append(f"Sequence: {seq}")

    return "\n".join(lines)


def format_calendar_list(calendars: list[dict[str, Any]]) -> str:
    """Format calendar list as compact lines."""
    if not calendars:
        return "(no calendars)"
    lines = []
    for cal in calendars:
        name = cal.get("name") or "(unnamed)"
        uid = cal.get("uid", "")
        provider = cal.get("provider", "")
        parts = [name]
        if provider and provider != "default":
            parts.append(f"provider={provider}")
        parts.append(f"uid={uid}")
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def format_info(info: dict[str, Any]) -> str:
    """Format health check info."""
    lines = []
    for p in info.get("providers", []):
        status = p.get("status", "unknown")
        name = p.get("name", "?")
        if status == "connected":
            lines.append(f"{name}: connected ({p.get('calendars', 0)} calendars)")
        else:
            lines.append(f"{name}: {status} — {p.get('error', 'unknown error')}")
    lines.append(f"Total calendars: {info.get('total_calendars', 0)}")
    lines.append(f"Write enabled: {info.get('write_enabled', False)}")
    return "\n".join(lines)


def format_freebusy(periods: list[dict[str, str]]) -> str:
    """Format free/busy periods as compact lines."""
    if not periods:
        return "(no busy periods — completely free)"
    lines = []
    for p in periods:
        start = _format_time(p.get("start"))
        end = _format_time(p.get("end"))
        lines.append(f"BUSY {start}-{end}")
    return "\n".join(lines)
