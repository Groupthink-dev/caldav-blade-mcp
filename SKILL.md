---
name: caldav-blade
description: CalDAV calendar operations — events, scheduling, availability, multi-provider
version: 0.1.0
permissions:
  read:
    - cal_info
    - cal_calendars
    - cal_events
    - cal_events_batch
    - cal_event
    - cal_search
    - cal_today
    - cal_week
    - cal_freebusy
  write:
    - cal_create
    - cal_update
    - cal_delete
    - cal_move
---

# CalDAV Blade MCP — Skill Guide

## Token Efficiency Rules (MANDATORY)

1. **Use `cal_events_batch` over multiple `cal_events` calls** — single call, grouped output
2. **Use `cal_today` / `cal_week` for common views** — zero-config convenience tools
3. **Use `cal_freebusy` for availability** — returns only busy periods, minimal tokens
4. **Use `cal_search` to find specific events** — avoids scanning full date ranges
5. **Use `cal_event` only when you need full details** — attendees, recurrence, description
6. **Never fetch all calendars then filter** — pass `calendar=` to scope queries

## Quick Start — 5 Most Common Operations

```
cal_today                                     → All events today
cal_week                                      → This week's events
cal_events calendar="Work" start=... end=...  → Events from one calendar
cal_events_batch calendars=["Work","Personal"] start=... end=...  → Multi-calendar
cal_freebusy start=... end=...                → Availability check
```

## Tool Reference

### Meta
- **cal_info** — Provider health check: connection status, calendar count, write gate.
- **cal_calendars** — List all calendars across all providers. Name, UID, provider.

### Events Read
- **cal_events** — Events from one calendar in a date range. Compact one-line output.
- **cal_events_batch** — Events from multiple calendars in one call. Grouped by calendar. Preferred for digests.
- **cal_event** — Full event detail by UID: attendees, recurrence, description.
- **cal_search** — Search by text, attendee email, or location. Optional calendar/date scope.
- **cal_today** — Today's events across all calendars. Zero-config.
- **cal_week** — This week's events across all calendars. `start_monday=true` (default) for Mon-Sun.
- **cal_freebusy** — Busy periods only. Very token-efficient for availability/scheduling.

### Events Write (requires CALDAV_WRITE_ENABLED=true)
- **cal_create** — Create event with title, start/end, optional description/location/recurrence/attendees/alarm.
- **cal_update** — Partial update by UID. Only changed fields sent. Auto-increments SEQUENCE.
- **cal_delete** — Delete by UID. Requires `confirm=true` safety gate.
- **cal_move** — Move event between calendars.

## Workflow Examples

### Morning Digest Data Gathering
```
1. cal_events_batch calendars=["Piers","Work","Personal"] start="2026-03-13T00:00:00+11:00" end="2026-03-13T23:59:59+11:00"
   → All personal events for today in one call
2. cal_events_batch calendars=["Kim","Monty","Estella","Nadia","Family","Household"] start=... end=...
   → All family events in one call
```

### Find Availability for Scheduling
```
1. cal_freebusy start="2026-03-14T09:00:00+11:00" end="2026-03-14T17:00:00+11:00"
   → See all busy periods on Friday
2. Identify gaps → suggest meeting times
```

### Schedule a Meeting
```
1. cal_freebusy start=... end=...              → Check availability
2. cal_create calendar="Work" title="Standup" start=... end=... alarm_minutes=10
   → Create with 10-minute reminder
```

### Find and Reschedule
```
1. cal_search query="Dentist"                  → Find the event
2. cal_update event_uid="uid123" start="2026-03-15T14:00:00+11:00" end="2026-03-15T15:00:00+11:00"
   → Move to new time (only changed fields)
```

### Weekly Digest (Batch Pattern)
```
1. cal_events_batch calendars=["Piers","Work","Personal","Piers travel","Review and Plan"] start=Monday end=Sunday
   → Personal week view
2. cal_events_batch calendars=["Kim","Monty","Estella","Nadia","Family","Household"] start=Monday end=Sunday
   → Family week view
```

## Common Parameters

| Parameter | Description | Example |
|-----------|-------------|---------|
| `calendar` | Calendar name or UID | `calendar="Work"` |
| `calendars` | List of calendar names/UIDs (batch) | `calendars=["Work","Personal"]` |
| `start` | Range start (ISO 8601 with timezone) | `start="2026-03-13T00:00:00+11:00"` |
| `end` | Range end (ISO 8601 with timezone) | `end="2026-03-13T23:59:59+11:00"` |
| `event_uid` | iCalendar UID | `event_uid="abc-123-def"` |
| `query` | Text search (title + description) | `query="Dentist"` |
| `attendee` | Filter by attendee email | `attendee="alice@example.com"` |
| `location` | Filter by location (substring) | `location="Level 3"` |
| `confirm` | Safety gate for delete | `confirm=true` |

## Output Format

Events use a compact pipe-delimited format to minimize token usage:

```
08:30-09:30 | Standup | uid=abc123
All day | School holidays | uid=def456
14:00-15:00 | Dentist @ Level 3 | uid=ghi789
```

Null fields are omitted. Times are local HH:MM. All-day events shown as "All day".

## Multi-Provider Support

Supports multiple CalDAV providers simultaneously (e.g. Fastmail + iCloud).
Set `CALDAV_PROVIDERS=fastmail,icloud` with per-provider credentials.
Calendar names are unique across providers — no prefix needed when querying.

## Security Notes

- Write operations blocked unless `CALDAV_WRITE_ENABLED=true`
- `cal_delete` additionally requires `confirm=true`
- Credentials never appear in tool output (scrubbed from error messages)
- Bearer token auth available for HTTP transport
- Provider passwords read from env vars only — never logged or cached
