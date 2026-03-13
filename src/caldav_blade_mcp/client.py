"""CalDAV client wrapper.

Wraps the ``caldav`` library with typed exceptions, credential scrubbing,
multi-provider support, and convenience methods. All methods are synchronous —
the server wraps them with ``asyncio.to_thread()``.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

import caldav
from caldav import DAVClient
from dateutil.parser import isoparse
from icalendar import vCalAddress, vRecur, vText

from caldav_blade_mcp.models import ProviderConfig, parse_providers

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class CalDAVError(Exception):
    """Base exception for CalDAV client errors."""

    def __init__(self, message: str, details: str = "") -> None:
        super().__init__(message)
        self.details = details


class AuthError(CalDAVError):
    """Authentication failed — invalid or expired credentials."""


class NotFoundError(CalDAVError):
    """Requested resource (calendar, event) not found."""


class ConnectionError(CalDAVError):  # noqa: A001
    """Cannot connect to CalDAV server."""


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------

_ERROR_PATTERNS: list[tuple[str, type[CalDAVError]]] = [
    ("unauthorized", AuthError),
    ("authentication", AuthError),
    ("invalid credentials", AuthError),
    ("forbidden", AuthError),
    ("not found", NotFoundError),
    ("does not exist", NotFoundError),
    ("no such", NotFoundError),
    ("connection", ConnectionError),
    ("timeout", ConnectionError),
    ("unreachable", ConnectionError),
]


def _classify_error(message: str) -> CalDAVError:
    """Map error message to a typed exception."""
    lower = message.lower()
    for pattern, exc_cls in _ERROR_PATTERNS:
        if pattern in lower:
            return exc_cls(message)
    return CalDAVError(message)


def _scrub_credentials(text: str) -> str:
    """Remove passwords and URLs with embedded auth from text."""
    # Strip URLs with embedded credentials
    text = re.sub(r"https?://[^:]+:[^@]+@", "https://****:****@", text)
    # Strip anything that looks like a password parameter
    text = re.sub(r"password=[^\s&]+", "password=****", text, flags=re.IGNORECASE)
    return text


# ---------------------------------------------------------------------------
# Event extraction
# ---------------------------------------------------------------------------


def _serialize_dt(dt: date | datetime | None) -> str | None:
    """Serialize a date or datetime to ISO 8601 string."""
    if dt is None:
        return None
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.isoformat()
    return dt.isoformat()


def _is_all_day(vevent: Any) -> bool:
    """Check if an event is all-day (DTSTART is a date, not datetime)."""
    dtstart = vevent.get("DTSTART")
    if dtstart is None:
        return False
    return isinstance(dtstart.dt, date) and not isinstance(dtstart.dt, datetime)


def _extract_event(vevent: Any) -> dict[str, Any]:
    """Extract a VEVENT component into a plain dict."""
    attendees = []
    raw_attendees = vevent.get("ATTENDEE", [])
    if not isinstance(raw_attendees, list):
        raw_attendees = [raw_attendees]
    for att in raw_attendees:
        partstat = att.params.get("PARTSTAT", "NEEDS-ACTION") if hasattr(att, "params") else "NEEDS-ACTION"
        cn = att.params.get("CN", str(att)) if hasattr(att, "params") else str(att)
        attendees.append({"email": str(att).replace("mailto:", ""), "name": str(cn), "status": str(partstat)})

    rrule = None
    if "RRULE" in vevent:
        rrule = vevent["RRULE"].to_ical().decode()

    return {
        "uid": str(vevent.get("UID", "")),
        "title": str(vevent.get("SUMMARY", "")),
        "description": str(vevent.get("DESCRIPTION", "")) if vevent.get("DESCRIPTION") else None,
        "location": str(vevent.get("LOCATION", "")) if vevent.get("LOCATION") else None,
        "start": _serialize_dt(vevent.get("DTSTART", {}).dt if vevent.get("DTSTART") else None),
        "end": _serialize_dt(vevent.get("DTEND", {}).dt if vevent.get("DTEND") else None),
        "all_day": _is_all_day(vevent),
        "recurrence_rule": rrule,
        "attendees": attendees if attendees else None,
        "sequence": int(str(vevent.get("SEQUENCE", 0))) if vevent.get("SEQUENCE") else 0,
    }


# ---------------------------------------------------------------------------
# Provider connection
# ---------------------------------------------------------------------------


class _ProviderConnection:
    """Lazy CalDAV connection for a single provider."""

    def __init__(self, config: ProviderConfig) -> None:
        self.config = config
        self._dav: DAVClient | None = None
        self._principal: Any = None

    def connect(self) -> None:
        if self._dav is None:
            self._dav = DAVClient(
                url=self.config.url,
                username=self.config.username,
                password=self.config.password,
            )
            self._principal = self._dav.principal()
            logger.info("Connected to CalDAV provider: %s", self.config.name)

    @property
    def principal(self) -> Any:
        self.connect()
        return self._principal

    def calendars(self) -> list[Any]:
        return list(self.principal.calendars())


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class CalDAVClient:
    """Multi-provider CalDAV client.

    Manages one ``DAVClient`` per provider (lazy-initialized). Calendar lookups
    search across all providers. All methods are synchronous — the MCP server's
    ``_run()`` helper wraps them in ``asyncio.to_thread()``.
    """

    def __init__(self, providers: list[ProviderConfig] | None = None) -> None:
        configs = providers or parse_providers()
        self._providers: dict[str, _ProviderConnection] = {cfg.name: _ProviderConnection(cfg) for cfg in configs}
        logger.info("CalDAVClient initialised with %d provider(s): %s", len(configs), ", ".join(self._providers))

    def _all_calendars(self) -> list[tuple[str, Any]]:
        """Return (provider_name, calendar) pairs across all providers."""
        result = []
        for name, conn in self._providers.items():
            try:
                for cal in conn.calendars():
                    result.append((name, cal))
            except Exception as e:
                msg = _scrub_credentials(str(e))
                logger.warning("Failed to list calendars from provider %s: %s", name, msg)
                raise _classify_error(msg) from e
        return result

    def _find_calendar(self, calendar: str) -> tuple[str, Any]:
        """Find a calendar by name or UID across all providers.

        Returns (provider_name, calendar_object).
        """
        for provider_name, cal in self._all_calendars():
            if str(cal.name) == calendar or str(cal.id) == calendar:
                return provider_name, cal
        raise NotFoundError(f"Calendar not found: {calendar!r}")

    def _find_event(self, event_uid: str, calendar: str | None = None) -> tuple[str, Any, Any]:
        """Find an event by UID. Returns (provider_name, calendar, event)."""
        if calendar:
            _, cal = self._find_calendar(calendar)
            try:
                event = cal.object_by_uid(event_uid)
                return "", cal, event
            except caldav.error.NotFoundError:
                raise NotFoundError(f"Event {event_uid!r} not found in calendar {calendar!r}") from None

        for provider_name, cal in self._all_calendars():
            try:
                event = cal.object_by_uid(event_uid)
                return provider_name, cal, event
            except caldav.error.NotFoundError:
                continue
            except Exception:
                continue
        raise NotFoundError(f"Event {event_uid!r} not found in any calendar")

    def _events_from_calendar(self, cal: Any, start: datetime, end: datetime) -> list[dict[str, Any]]:
        """Fetch and extract events from a single calendar."""
        results = cal.search(start=start, end=end, event=True, expand=True)
        events = []
        for obj in results:
            for comp in obj.icalendar_instance.subcomponents:
                if comp.name == "VEVENT":
                    events.append(_extract_event(comp))
        return events

    # ------------------------------------------------------------------
    # Read API
    # ------------------------------------------------------------------

    def list_calendars(self) -> list[dict[str, Any]]:
        """Return all accessible calendars across all providers."""
        result = []
        for provider_name, cal in self._all_calendars():
            result.append(
                {
                    "name": str(cal.name) if cal.name else None,
                    "uid": str(cal.id),
                    "provider": provider_name,
                }
            )
        return result

    def info(self) -> dict[str, Any]:
        """Health check: providers, connection status, calendar count."""
        providers_status = []
        total_calendars = 0
        for name, conn in self._providers.items():
            try:
                cals = conn.calendars()
                total_calendars += len(cals)
                providers_status.append({"name": name, "status": "connected", "calendars": len(cals)})
            except Exception as e:
                providers_status.append({"name": name, "status": "error", "error": _scrub_credentials(str(e))})
        return {
            "providers": providers_status,
            "total_calendars": total_calendars,
            "write_enabled": os.environ.get("CALDAV_WRITE_ENABLED", "").lower() == "true",
        }

    def get_events(self, calendar: str, start: str, end: str) -> list[dict[str, Any]]:
        """Get events from a single calendar in a date range."""
        _, cal = self._find_calendar(calendar)
        dtstart = isoparse(start)
        dtend = isoparse(end)
        return self._events_from_calendar(cal, dtstart, dtend)

    def get_events_batch(self, calendars: list[str], start: str, end: str) -> dict[str, list[dict[str, Any]]]:
        """Get events from multiple calendars in one call, grouped by calendar name."""
        dtstart = isoparse(start)
        dtend = isoparse(end)
        result: dict[str, list[dict[str, Any]]] = {}
        for cal_name in calendars:
            try:
                _, cal = self._find_calendar(cal_name)
                display_name = str(cal.name) if cal.name else cal_name
                result[display_name] = self._events_from_calendar(cal, dtstart, dtend)
            except Exception as exc:
                logger.warning("Failed to get events for calendar %s: %s", cal_name, _scrub_credentials(str(exc)))
                result[cal_name] = [{"error": _scrub_credentials(str(exc))}]
        return result

    def get_event(self, event_uid: str, calendar: str | None = None) -> dict[str, Any]:
        """Get a single event by UID with full details."""
        _, _, event = self._find_event(event_uid, calendar)
        for comp in event.icalendar_instance.subcomponents:
            if comp.name == "VEVENT":
                return _extract_event(comp)
        raise NotFoundError(f"VEVENT component not found for UID {event_uid!r}")

    def search_events(
        self,
        query: str | None = None,
        attendee: str | None = None,
        location: str | None = None,
        calendar: str | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search events by text, attendee, or location."""
        if calendar:
            search_cals = [self._find_calendar(calendar)]
        else:
            search_cals = [(n, c) for n, c in self._all_calendars()]

        dtstart = isoparse(start) if start else None
        dtend = isoparse(end) if end else None
        results: list[dict[str, Any]] = []

        for _, cal in search_cals:
            try:
                objs = cal.search(start=dtstart, end=dtend, event=True, expand=True)
                for obj in objs:
                    for comp in obj.icalendar_instance.subcomponents:
                        if comp.name != "VEVENT":
                            continue
                        ev = _extract_event(comp)
                        if query:
                            text = " ".join(filter(None, [ev.get("title"), ev.get("description")])).lower()
                            if query.lower() not in text:
                                continue
                        if attendee:
                            emails = [a["email"].lower() for a in (ev.get("attendees") or [])]
                            if attendee.lower() not in emails:
                                continue
                        if location:
                            if location.lower() not in (ev.get("location") or "").lower():
                                continue
                        results.append(ev)
            except Exception as exc:
                logger.warning("Failed to search calendar %s: %s", cal.name, _scrub_credentials(str(exc)))
        return results

    def get_today(self) -> dict[str, list[dict[str, Any]]]:
        """Get today's events across all calendars, grouped by calendar."""
        now = datetime.now(tz=UTC)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_start + timedelta(days=1)
        result: dict[str, list[dict[str, Any]]] = {}
        for _, cal in self._all_calendars():
            name = str(cal.name) if cal.name else str(cal.id)
            try:
                events = self._events_from_calendar(cal, today_start, today_end)
                if events:
                    result[name] = events
            except Exception as exc:
                logger.warning("Failed to get today events for %s: %s", name, _scrub_credentials(str(exc)))
        return result

    def get_week(self, start_monday: bool = True) -> dict[str, list[dict[str, Any]]]:
        """Get this week's events across all calendars, grouped by calendar."""
        now = datetime.now(tz=UTC)
        if start_monday:
            days_since_monday = now.weekday()
            week_start = (now - timedelta(days=days_since_monday)).replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            week_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_end = week_start + timedelta(days=7)
        result: dict[str, list[dict[str, Any]]] = {}
        for _, cal in self._all_calendars():
            name = str(cal.name) if cal.name else str(cal.id)
            try:
                events = self._events_from_calendar(cal, week_start, week_end)
                if events:
                    result[name] = events
            except Exception as exc:
                logger.warning("Failed to get week events for %s: %s", name, _scrub_credentials(str(exc)))
        return result

    def freebusy(self, start: str, end: str, calendar: str | None = None) -> list[dict[str, str]]:
        """Query free/busy for a date range. Returns busy periods only."""
        dtstart = isoparse(start)
        dtend = isoparse(end)

        if calendar:
            search_cals = [self._find_calendar(calendar)]
        else:
            search_cals = [(n, c) for n, c in self._all_calendars()]

        busy_periods: list[dict[str, str]] = []
        for _, cal in search_cals:
            try:
                fb = cal.freebusy_request(dtstart, dtend)
                if fb and hasattr(fb, "instance"):
                    for comp in fb.instance.subcomponents:
                        if comp.name == "VFREEBUSY":
                            for fb_prop in comp.get("FREEBUSY", []):
                                if not isinstance(fb_prop, list):
                                    fb_prop = [fb_prop]
                                for period in fb_prop:
                                    if hasattr(period, "dt"):
                                        busy_periods.append(
                                            {
                                                "start": _serialize_dt(period.dt) or "",
                                                "end": "",
                                            }
                                        )
                                    elif hasattr(period, "__iter__") and len(period) == 2:
                                        busy_periods.append(
                                            {
                                                "start": _serialize_dt(period[0]) or "",
                                                "end": _serialize_dt(period[1]) or "",
                                            }
                                        )
            except Exception:
                # Freebusy not supported by all providers — fall back to event scan
                events = self._events_from_calendar(cal, dtstart, dtend)
                for ev in events:
                    if ev.get("start") and ev.get("end"):
                        busy_periods.append(
                            {
                                "start": ev["start"],
                                "end": ev["end"],
                            }
                        )
        # Sort by start time
        busy_periods.sort(key=lambda p: p["start"])
        return busy_periods

    # ------------------------------------------------------------------
    # Write API
    # ------------------------------------------------------------------

    def create_event(
        self,
        calendar: str,
        title: str,
        start: str,
        end: str,
        description: str | None = None,
        location: str | None = None,
        recurrence_rule: str | None = None,
        attendees: list[dict[str, str]] | None = None,
        alarm_minutes: int | None = None,
    ) -> dict[str, Any]:
        """Create a new event. Returns the created event."""
        _, cal = self._find_calendar(calendar)
        dtstart = isoparse(start)
        dtend = isoparse(end)

        kwargs: dict[str, Any] = {"dtstart": dtstart, "dtend": dtend, "summary": title}
        if description:
            kwargs["description"] = description
        if location:
            kwargs["location"] = location

        event = cal.save_event(**kwargs)

        # Apply fields not supported by save_event kwargs
        needs_edit = any([recurrence_rule, attendees, alarm_minutes is not None])
        if needs_edit:
            with event.edit_icalendar_instance() as ical:
                for comp in ical.subcomponents:
                    if comp.name != "VEVENT":
                        continue
                    if recurrence_rule:
                        comp["RRULE"] = vRecur.from_ical(recurrence_rule)
                    if attendees:
                        for att in attendees:
                            a = vCalAddress(f"mailto:{att['email']}")
                            a.params["CN"] = att.get("name", att["email"])
                            a.params["PARTSTAT"] = att.get("status", "NEEDS-ACTION")
                            comp.add("ATTENDEE", a)
                    if alarm_minutes is not None:
                        from icalendar import Alarm

                        alarm = Alarm()
                        alarm.add("action", "DISPLAY")
                        alarm.add("trigger", timedelta(minutes=-alarm_minutes))
                        comp.add_component(alarm)

        # Re-fetch for canonical representation
        uid = str(event.icalendar_instance.subcomponents[0].get("UID", ""))
        _, _, refreshed = self._find_event(uid, calendar)
        for comp in refreshed.icalendar_instance.subcomponents:
            if comp.name == "VEVENT":
                return _extract_event(comp)
        return {"uid": uid, "title": title}

    def update_event(
        self,
        event_uid: str,
        calendar: str | None = None,
        title: str | None = None,
        start: str | None = None,
        end: str | None = None,
        description: str | None = None,
        location: str | None = None,
        recurrence_rule: str | None = None,
    ) -> dict[str, Any]:
        """Partial update by UID. Only changed fields sent. Auto-increments SEQUENCE."""
        if all(v is None for v in (title, start, end, description, location, recurrence_rule)):
            return self.get_event(event_uid, calendar)

        _, _, event = self._find_event(event_uid, calendar)

        with event.edit_icalendar_instance() as ical:
            for comp in ical.subcomponents:
                if comp.name != "VEVENT":
                    continue

                if title is not None:
                    comp["SUMMARY"] = vText(title)

                if start is not None:
                    if "DTSTART" in comp:
                        del comp["DTSTART"]
                    comp.add("DTSTART", isoparse(start))

                if end is not None:
                    if "DTEND" in comp:
                        del comp["DTEND"]
                    comp.add("DTEND", isoparse(end))

                if description is not None:
                    if "DESCRIPTION" in comp:
                        del comp["DESCRIPTION"]
                    if description:
                        comp["DESCRIPTION"] = vText(description)

                if location is not None:
                    if "LOCATION" in comp:
                        del comp["LOCATION"]
                    if location:
                        comp["LOCATION"] = vText(location)

                if recurrence_rule is not None:
                    if "RRULE" in comp:
                        del comp["RRULE"]
                    if recurrence_rule:
                        comp["RRULE"] = vRecur.from_ical(recurrence_rule)

                # RFC 5545 §3.8.7.4: increment SEQUENCE
                current_seq = int(str(comp.get("SEQUENCE", 0)))
                if "SEQUENCE" in comp:
                    del comp["SEQUENCE"]
                comp.add("SEQUENCE", current_seq + 1)

                now = datetime.now(tz=UTC)
                for field in ("LAST-MODIFIED", "DTSTAMP"):
                    if field in comp:
                        del comp[field]
                comp.add("LAST-MODIFIED", now)
                comp.add("DTSTAMP", now)

        # Re-fetch canonical
        _, _, refreshed = self._find_event(event_uid, calendar)
        for comp in refreshed.icalendar_instance.subcomponents:
            if comp.name == "VEVENT":
                return _extract_event(comp)
        raise NotFoundError(f"VEVENT missing after update for UID {event_uid!r}")

    def delete_event(self, event_uid: str, calendar: str | None = None) -> bool:
        """Delete an event by UID. Returns True if deleted."""
        _, _, event = self._find_event(event_uid, calendar)
        event.delete()
        return True

    def move_event(self, event_uid: str, from_calendar: str, to_calendar: str) -> dict[str, Any]:
        """Move an event between calendars (delete + create preserving data)."""
        event_data = self.get_event(event_uid, from_calendar)
        self.delete_event(event_uid, from_calendar)

        return self.create_event(
            calendar=to_calendar,
            title=event_data.get("title", ""),
            start=event_data.get("start", ""),
            end=event_data.get("end", ""),
            description=event_data.get("description"),
            location=event_data.get("location"),
            recurrence_rule=event_data.get("recurrence_rule"),
        )
