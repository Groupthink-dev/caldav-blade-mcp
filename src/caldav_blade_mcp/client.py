"""CalDAV client wrapper.

Wraps the ``caldav`` library with typed exceptions, credential scrubbing,
multi-provider support, and convenience methods. All methods are synchronous —
the server wraps them with ``asyncio.to_thread()``.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import caldav
import caldav.lib.error
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
    ("412", NotFoundError),
    ("precondition", NotFoundError),
    ("connection", ConnectionError),
    ("timeout", ConnectionError),
    ("unreachable", ConnectionError),
    ("report", CalDAVError),
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


def _local_tz() -> tzinfo:
    """Resolve the configured local timezone."""
    name = os.environ.get("CALDAV_LOCAL_TZ", "").strip()
    if name:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise CalDAVError(f"Invalid CALDAV_LOCAL_TZ {name!r}: {exc}") from exc
    local_tz = datetime.now().astimezone().tzinfo
    if local_tz is None:
        raise CalDAVError("Unable to resolve the system local timezone")
    return local_tz


def local_day_window(now: datetime | None = None) -> tuple[datetime, datetime]:
    """Return [00:00, 24:00) of today in the configured local timezone."""
    if now is None:
        now = datetime.now(tz=_local_tz())
    else:
        now = now.astimezone(_local_tz())
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def _serialize_dt(dt: date | datetime | None) -> str | None:
    """Serialize a date or datetime to ISO 8601 string."""
    if dt is None:
        return None
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.isoformat()
    return dt.isoformat()


def _parse_dt(value: str, all_day: bool | None = None) -> date | datetime:
    """Parse an ISO 8601 string into a ``date`` (all-day) or ``datetime`` (timed).

    Returning a bare ``date`` makes both ``cal.save_event(dtstart=<date>, ...)``
    and ``comp.add("DTSTART", <date>)`` emit ``VALUE=DATE`` per icalendar's type
    dispatch — exactly what the read-path discriminator ``_is_all_day`` detects.

    - ``all_day=True``: force date-only construction. A full datetime input is
      truncated to its ``.date()``.
    - ``all_day=None`` (default, auto-detect): a date-only string
      (``len == 10`` and no ``T``) parses as ``date``; otherwise ``datetime``.
      Preserves backwards-compat for existing timed-event callers.
    - ``all_day=False``: always parse as ``datetime``.
    """
    if all_day is True:
        parsed: date = isoparse(value).date()
        return parsed
    if all_day is None and len(value) == 10 and "T" not in value:
        return date.fromisoformat(value)
    dt: datetime = isoparse(value)
    return dt


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

    # VALARM extraction (D8): surface the first relative-negative-duration
    # TRIGGER as alarm_minutes so move_event can re-create the reminder.
    alarm_minutes = None
    for sub in getattr(vevent, "subcomponents", []) or []:
        if getattr(sub, "name", None) != "VALARM":
            continue
        trigger = sub.get("TRIGGER")
        trig_dt = getattr(trigger, "dt", None) if trigger is not None else None
        if isinstance(trig_dt, timedelta) and trig_dt.total_seconds() < 0:
            alarm_minutes = int(-trig_dt.total_seconds() // 60)
            break

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
        "alarm_minutes": alarm_minutes,
        "sequence": int(str(vevent.get("SEQUENCE", 0))) if vevent.get("SEQUENCE") else 0,
    }


def _overlaps_window(ev: dict[str, Any], start: datetime, end: datetime) -> bool:
    """Return whether an event overlaps [start, end) using RFC 5545 semantics."""
    raw_start = ev.get("start")
    if raw_start is None:
        return True

    event_start = _parse_dt(raw_start)
    raw_end = ev.get("end")

    if isinstance(event_start, date) and not isinstance(event_start, datetime):
        event_end = _parse_dt(raw_end) if raw_end is not None else None
        if isinstance(event_end, datetime):
            event_end = event_end.date()
        if event_end is None or event_end == event_start:
            event_end = event_start + timedelta(days=1)
        assert isinstance(event_end, date) and not isinstance(event_end, datetime)
        local_tz = start.tzinfo or UTC
        window_event_start = datetime.combine(event_start, time.min, tzinfo=local_tz)
        window_event_end = datetime.combine(event_end, time.min, tzinfo=local_tz)
        return window_event_start < end and window_event_end > start

    assert isinstance(event_start, datetime)
    if event_start.tzinfo is None:
        event_start = event_start.replace(tzinfo=UTC)
    event_end = _parse_dt(raw_end) if raw_end is not None else None
    if isinstance(event_end, date) and not isinstance(event_end, datetime):
        event_end = datetime.combine(event_end, time.min, tzinfo=UTC)
    if isinstance(event_end, datetime) and event_end.tzinfo is None:
        event_end = event_end.replace(tzinfo=UTC)
    if event_end is None or event_end == event_start:
        return start <= event_start < end
    assert isinstance(event_end, datetime)
    return event_start < end and event_end > start


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
            dav = DAVClient(
                url=self.config.url,
                username=self.config.username,
                password=self.config.password,
            )
            # D5: principal() is the fallible auth step. Assign the cached handle
            # ONLY after it succeeds — otherwise an auth failure leaves _dav set
            # but _principal None, the `_dav is None` guard never re-attempts, and
            # every later call returns None -> a misleading AttributeError instead
            # of the real AuthorizationError. Roll back on failure so the next call
            # re-attempts and re-surfaces the true error.
            try:
                self._principal = dav.principal()
            except Exception:
                self._dav = None
                self._principal = None
                raise
            self._dav = dav
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

    def _search_event_by_uid(self, cal: Any, event_uid: str) -> Any | None:
        """Resolve an event object by UID via ``cal.search`` (iCloud-safe).

        D2/D4: ``object_by_uid`` issues a dateless REPORT that iCloud answers
        with a 412, so we use the same ``cal.search(event=True)`` path that the
        read methods already use successfully. NO ``expand`` (a dateless expand
        also 412s on iCloud). Returns the owning caldav *object* (the one
        exposing ``.icalendar_instance`` / ``.delete()`` /
        ``.edit_icalendar_instance()``) so the five ``_find_event`` callers keep
        working — not a bare VEVENT comp.
        """
        objs = cal.search(event=True)
        for obj in objs:
            for comp in obj.icalendar_instance.subcomponents:
                if comp.name == "VEVENT" and str(comp.get("UID")) == event_uid:
                    return obj
        return None

    def _find_event(self, event_uid: str, calendar: str | None = None) -> tuple[str, Any, Any]:
        """Find an event by UID. Returns (provider_name, calendar, event)."""
        if calendar:
            _, cal = self._find_calendar(calendar)
            try:
                event = self._search_event_by_uid(cal, event_uid)
            except caldav.lib.error.DAVError as exc:
                raise _classify_error(_scrub_credentials(str(exc))) from exc
            if event is None:
                raise NotFoundError(f"Event {event_uid!r} not found in calendar {calendar!r}")
            return "", cal, event

        for provider_name, cal in self._all_calendars():
            try:
                event = self._search_event_by_uid(cal, event_uid)
            except Exception as exc:
                logger.warning("Failed to search calendar %s: %s", cal.name, _scrub_credentials(str(exc)))
                continue
            if event is not None:
                return provider_name, cal, event
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
        """Return all accessible calendars across all providers.

        DD-338 B.2: partial-tolerant — a single failing provider no longer
        kills the whole tool. Each provider is tried independently; failures
        surface as in-band error rows (``{"provider": name, "error": msg}``)
        alongside the standard calendar rows, mirroring the per-provider
        try/except pattern already used by ``info()`` (see ``client.py``
        :meth:`info`). This closes the latent gap where a slow iCloud would
        previously block the Fastmail/Google listing.

        The declaration in ``stallari-plugins/plugins/tools/caldav-blade-mcp.json``
        is ``deterministic_ordering: unsorted`` per the architect-ratified
        honest-degraded-declaration precedent (cf_d1_query); no internal sort
        is applied — the assembler downstream is responsible for canonical
        ordering with full provenance.
        """
        result: list[dict[str, Any]] = []
        # DD-338 B.2: per-provider try/except (was "raise on first failure")
        for provider_name, conn in self._providers.items():
            try:
                for cal in conn.calendars():
                    result.append(
                        {
                            "name": str(cal.name) if cal.name else None,
                            "uid": str(cal.id),
                            "provider": provider_name,
                        }
                    )
            except Exception as exc:
                msg = _scrub_credentials(str(exc))
                logger.warning("Failed to list calendars from provider %s: %s", provider_name, msg)
                result.append({"provider": provider_name, "error": msg})
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

        # D3: expand=True is rejected by iCloud/caldav when no date range is given
        # ("can't expand without a date range"). A dateless search (query/attendee/
        # location only) MUST NOT pass expand=True, or it raises and the broad except
        # below silently returns a false-empty result on every calendar. Only expand
        # when a date range is present.
        expand = bool(dtstart and dtend)
        for _, cal in search_cals:
            try:
                objs = cal.search(start=dtstart, end=dtend, event=True, expand=expand)
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
        today_start, today_end = local_day_window()
        result: dict[str, list[dict[str, Any]]] = {}
        for _, cal in self._all_calendars():
            name = str(cal.name) if cal.name else str(cal.id)
            try:
                events = self._events_from_calendar(cal, today_start, today_end)
                events = [event for event in events if _overlaps_window(event, today_start, today_end)]
                if events:
                    result[name] = events
            except Exception as exc:
                logger.warning("Failed to get today events for %s: %s", name, _scrub_credentials(str(exc)))
        return result

    def get_week(self, start_monday: bool = True) -> dict[str, list[dict[str, Any]]]:
        """Get this week's events across all calendars, grouped by calendar."""
        now = datetime.now(tz=_local_tz())
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
                events = [event for event in events if _overlaps_window(event, week_start, week_end)]
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
        all_day: bool | None = None,
    ) -> dict[str, Any]:
        """Create a new event. Returns the created event."""
        _, cal = self._find_calendar(calendar)
        dtstart = _parse_dt(start, all_day)
        dtend = _parse_dt(end, all_day)

        kwargs: dict[str, Any] = {"dtstart": dtstart, "dtend": dtend, "summary": title}
        if description:
            kwargs["description"] = description
        if location:
            kwargs["location"] = location

        try:
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
                # D13: edit_icalendar_instance() only BORROWS the object for
                # in-memory editing — it does NOT PUT on context exit. Without an
                # explicit save() the recurrence/attendee/alarm fields are silently
                # dropped on the wire (proven live on iCloud). Persist them.
                event.save()
        except caldav.lib.error.DAVError as exc:
            raise _classify_error(_scrub_credentials(str(exc))) from exc

        # The PUT has landed; the UID is known. A re-fetch failure must NOT turn a
        # successful write into a reported failure (D2). Best-effort canonical re-fetch.
        uid = str(event.icalendar_instance.subcomponents[0].get("UID", ""))
        try:
            _, _, refreshed = self._find_event(uid, calendar)
            for comp in refreshed.icalendar_instance.subcomponents:
                if comp.name == "VEVENT":
                    return _extract_event(comp)
        except (CalDAVError, caldav.lib.error.DAVError, Exception) as exc:
            logger.warning("Re-fetch after create failed for UID %s: %s", uid, _scrub_credentials(str(exc)))
        return {
            "uid": uid,
            "title": title,
            "start": start,
            "end": end,
            "description": description,
            "location": location,
            "recurrence_rule": recurrence_rule,
            "attendees": attendees,
            "all_day": all_day,
        }

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
        all_day: bool | None = None,
    ) -> dict[str, Any]:
        """Partial update by UID. Only changed fields sent. Auto-increments SEQUENCE."""
        if all(v is None for v in (title, start, end, description, location, recurrence_rule)):
            return self.get_event(event_uid, calendar)

        _, _, event = self._find_event(event_uid, calendar)

        try:
            with event.edit_icalendar_instance() as ical:
                for comp in ical.subcomponents:
                    if comp.name != "VEVENT":
                        continue

                    if title is not None:
                        comp["SUMMARY"] = vText(title)

                    if start is not None:
                        if "DTSTART" in comp:
                            del comp["DTSTART"]
                        comp.add("DTSTART", _parse_dt(start, all_day))

                    if end is not None:
                        if "DTEND" in comp:
                            del comp["DTEND"]
                        comp.add("DTEND", _parse_dt(end, all_day))

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
            # D13: edit_icalendar_instance() only BORROWS for in-memory editing —
            # it does NOT PUT on exit. Without this save() the update is silently
            # discarded (proven live on iCloud: the rename never reached the wire).
            event.save()
        except caldav.lib.error.DAVError as exc:
            raise _classify_error(_scrub_credentials(str(exc))) from exc

        # The update PUT has landed; a re-fetch failure must NOT turn a successful
        # update into a reported failure (D2 symmetry with create).
        try:
            _, _, refreshed = self._find_event(event_uid, calendar)
            for comp in refreshed.icalendar_instance.subcomponents:
                if comp.name == "VEVENT":
                    return _extract_event(comp)
        except (CalDAVError, caldav.lib.error.DAVError, Exception) as exc:
            logger.warning("Re-fetch after update failed for UID %s: %s", event_uid, _scrub_credentials(str(exc)))
        best_effort: dict[str, Any] = {"uid": event_uid}
        for key, val in (
            ("title", title),
            ("start", start),
            ("end", end),
            ("description", description),
            ("location", location),
            ("recurrence_rule", recurrence_rule),
            ("all_day", all_day),
        ):
            if val is not None:
                best_effort[key] = val
        return best_effort

    def delete_event(self, event_uid: str, calendar: str | None = None) -> bool:
        """Delete an event by UID. Returns True if deleted."""
        _, _, event = self._find_event(event_uid, calendar)
        try:
            event.delete()
        except caldav.lib.error.DAVError as exc:
            raise _classify_error(_scrub_credentials(str(exc))) from exc
        return True

    def move_event(self, event_uid: str, from_calendar: str, to_calendar: str) -> dict[str, Any]:
        """Move an event between calendars: create-at-dest FIRST, verify, delete-source LAST.

        D8/D9/D10: the prior implementation deleted the source BEFORE creating at
        the destination — an unguarded, lossy ordering where any create-leg
        failure left the event destroyed. This rewrite never reaches the
        irreversible delete unless the create has landed and been verified, and
        threads full fidelity (attendees, alarm, all_day) so an all-day or
        reminder-bearing event survives the move.
        """
        event_data = self.get_event(event_uid, from_calendar)

        # 1+2. Create at destination FIRST, threading full fidelity. A create-leg
        # failure raises here and NEVER reaches the source delete (D9 core).
        created = self.create_event(
            calendar=to_calendar,
            title=event_data.get("title", ""),
            start=event_data.get("start", ""),
            end=event_data.get("end", ""),
            description=event_data.get("description"),
            location=event_data.get("location"),
            recurrence_rule=event_data.get("recurrence_rule"),
            attendees=event_data.get("attendees"),
            alarm_minutes=event_data.get("alarm_minutes"),
            all_day=event_data.get("all_day"),
        )

        # 3. Verify the create landed before deleting. create_event is best-effort
        # non-fatal (D2); confirm the new event resolves on the destination.
        new_uid = str(created.get("uid", "")) if isinstance(created, dict) else ""
        if not new_uid:
            raise CalDAVError(f"Move aborted: create at {to_calendar!r} returned no UID; source preserved")
        try:
            _, dest_cal = self._find_calendar(to_calendar)
            verified = self._search_event_by_uid(dest_cal, new_uid)
        except Exception as exc:
            raise CalDAVError(
                f"Move aborted: could not verify create at {to_calendar!r} ({_scrub_credentials(str(exc))}); "
                "source preserved"
            ) from exc
        if verified is None:
            raise CalDAVError(f"Move aborted: created event {new_uid!r} not found at {to_calendar!r}; source preserved")

        # 4. Delete source LAST. A delete failure after a confirmed create is a
        # partial success — the copy exists, so never report a blanket failure.
        try:
            self.delete_event(event_uid, from_calendar)
        except Exception as exc:
            logger.warning(
                "Move: created at %s but source delete failed: %s", to_calendar, _scrub_credentials(str(exc))
            )
            result = dict(created)
            result["move_status"] = f"copied to {to_calendar} but source not removed"
            return result

        return created
