"""Token-efficient output formatters for CalDAV Blade MCP server.

All formatters return compact strings optimised for LLM consumption:
- One line per event
- Pipe-delimited fields
- Null-field omission
- Times in HH:MM 24h format
"""

from __future__ import annotations

import json
from typing import Any

from dateutil.parser import isoparse

# ---------------------------------------------------------------------------
# DD-338 Phase C Wave 2 — _meta envelope (JSON-tail block)
# ---------------------------------------------------------------------------
#
# Canonical wire shape (architect amendment 2026-05-21, mastodon A.1 precedent):
#
#     <existing payload>
#
#     _meta: {"matched_total": N, "returned": M, "filtered_by": [...], ...}
#
# Single JSON line, appended after \n\n. Assembler regex:
#     \n\n_meta: (\{.*\})$
#
# Required fields: matched_total, returned, filtered_by, latency_ms.
# Optional: redactions, next_cursor, error_notes.


def _append_meta(body: str, meta: dict[str, Any] | None) -> str:
    """Append a DD-338 _meta envelope as a JSON-tail block.

    Regex contract (assembler-side): ``\\n\\n_meta: (\\{.*\\})$``
    Required fields: ``matched_total``, ``returned``, ``filtered_by``,
    ``latency_ms``. Optional: ``redactions``, ``next_cursor``, ``error_notes``.

    Returns ``body`` unchanged when ``meta`` is ``None`` (back-compat for
    write-tool formatters that don't emit an envelope).
    """
    if meta is None:
        return body
    envelope = "_meta: " + json.dumps(meta, separators=(",", ":"), ensure_ascii=False)
    if not body:
        return envelope
    return f"{body}\n\n{envelope}"


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


def format_event_list(
    events: list[dict[str, Any]],
    *,
    meta: dict[str, Any] | None = None,
) -> str:
    """Format a list of events as compact lines.

    DD-338 Phase C Wave 2 — when ``meta`` is provided, appends a ``_meta``
    JSON-tail envelope per the structured audit-surface contract.
    """
    if not events:
        return _append_meta("(no events)", meta)
    # Sort by start time
    events = sorted(events, key=lambda e: e.get("start") or "")
    body = "\n".join(format_event_line(e) for e in events)
    return _append_meta(body, meta)


def format_events_grouped(
    grouped: dict[str, list[dict[str, Any]]],
    *,
    meta: dict[str, Any] | None = None,
) -> str:
    """Format events grouped by calendar name.

    DD-338 Phase C Wave 2 — appends an optional ``_meta`` envelope.
    """
    if not grouped:
        return _append_meta("(no events)", meta)
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
    body = "\n".join(lines).rstrip()
    return _append_meta(body, meta)


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


def format_calendar_list(
    calendars: list[dict[str, Any]],
    *,
    meta: dict[str, Any] | None = None,
) -> str:
    """Format calendar list as compact lines.

    DD-338 B.2: rows with an ``error`` key (and no ``name`` / ``uid``) render
    as in-band provenance warnings on their own line — e.g.
    ``⚠ icloud: Connection timeout (no calendars listed)``. This surfaces
    partial-provider failures without breaking the existing line-per-row
    output contract.

    DD-338 Phase C Wave 2 — appends an optional ``_meta`` envelope. When
    ``error_notes`` is populated in ``meta`` it carries the per-provider
    failure rows in structured form (the in-band ⚠-prefix rows remain for
    human-readable output).
    """
    if not calendars:
        return _append_meta("(no calendars)", meta)
    lines = []
    for cal in calendars:
        # DD-338 B.2: error-row variant — partial-provider failure provenance
        if "error" in cal and not cal.get("uid") and not cal.get("name"):
            provider = cal.get("provider", "?")
            lines.append(f"⚠ {provider}: {cal['error']} (no calendars listed)")
            continue
        name = cal.get("name") or "(unnamed)"
        uid = cal.get("uid", "")
        provider = cal.get("provider", "")
        parts = [name]
        if provider and provider != "default":
            parts.append(f"provider={provider}")
        parts.append(f"uid={uid}")
        lines.append(" | ".join(parts))
    body = "\n".join(lines)
    return _append_meta(body, meta)


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


def format_freebusy(
    periods: list[dict[str, str]],
    *,
    meta: dict[str, Any] | None = None,
) -> str:
    """Format free/busy periods as compact lines.

    DD-338 Phase C Wave 2 — appends an optional ``_meta`` envelope.
    """
    if not periods:
        return _append_meta("(no busy periods — completely free)", meta)
    lines = []
    for p in periods:
        start = _format_time(p.get("start"))
        end = _format_time(p.get("end"))
        lines.append(f"BUSY {start}-{end}")
    body = "\n".join(lines)
    return _append_meta(body, meta)
