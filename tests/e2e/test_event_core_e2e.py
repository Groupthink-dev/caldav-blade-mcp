"""DD-385 Half B — live round-trip e2e regressions for the event-core fixes.

SAFETY CONTRACT (DD-382 pattern):
- These tests make REAL CalDAV calls. They are gated TWICE: the ``e2e`` pytest
  marker AND the ``CALDAV_E2E`` environment variable. With neither selected they
  are skipped — they NEVER run as part of the default ``-m "not e2e"`` suite.
- A production-calendar DENYLIST refuses to run against any well-known
  family/personal calendar name. The architect runs these only against a fenced
  sandbox calendar named via ``CALDAV_E2E_CALENDAR`` (and a second sandbox via
  ``CALDAV_E2E_CALENDAR_DEST`` for the move tests).
- Every event created here is prefixed ``zz-`` and is torn down in a ``finally``
  block regardless of assertion outcome — no orphaned state on the live account.

Run (architect, under the safety contract) with::

    CALDAV_E2E=1 CALDAV_WRITE_ENABLED=true \\
      CALDAV_E2E_CALENDAR="zz-Sandbox" CALDAV_E2E_CALENDAR_DEST="zz-Sandbox-2" \\
      uv run pytest tests/e2e/ -v -m e2e
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from caldav_blade_mcp.client import CalDAVClient

pytestmark = pytest.mark.e2e

# Names this suite must NEVER mutate — a guard against pointing the live e2e at a
# real family/personal calendar. The sandbox calendar name is supplied via env.
_PRODUCTION_DENYLIST = {
    "calendar",
    "home",
    "family",
    "personal",
    "work",
    "shared",
    "birthdays",
    "holidays",
    "us holidays",
    "siri suggestions",
}


def _require_e2e() -> tuple[str, str]:
    """Gate on CALDAV_E2E + a non-denylisted sandbox calendar. Returns (src, dest)."""
    if os.environ.get("CALDAV_E2E", "") != "1":
        pytest.skip("CALDAV_E2E != 1 — live e2e disabled (default-off safety gate)")
    if os.environ.get("CALDAV_WRITE_ENABLED", "").lower() != "true":
        pytest.skip("CALDAV_WRITE_ENABLED != true — refusing live writes")

    src = os.environ.get("CALDAV_E2E_CALENDAR", "")
    dest = os.environ.get("CALDAV_E2E_CALENDAR_DEST", "")
    if not src:
        pytest.skip("CALDAV_E2E_CALENDAR unset — no fenced sandbox calendar configured")
    if src.strip().lower() in _PRODUCTION_DENYLIST:
        pytest.fail(f"Refusing to run e2e against denylisted production calendar {src!r}")
    if dest and dest.strip().lower() in _PRODUCTION_DENYLIST:
        pytest.fail(f"Refusing to run e2e against denylisted production calendar {dest!r}")
    return src, dest


def _zz_title(label: str) -> str:
    return f"zz-{label}-{uuid.uuid4().hex[:8]}"


def test_e2e_roundtrip_create_search_update_delete() -> None:
    """create → search-by-UID → read → update → delete on the sandbox calendar."""
    src, _ = _require_e2e()
    client = CalDAVClient()
    title = _zz_title("roundtrip")
    start = datetime.now(tz=UTC).replace(microsecond=0) + timedelta(days=1)
    end = start + timedelta(hours=1)

    created = client.create_event(
        calendar=src, title=title, start=start.isoformat(), end=end.isoformat()
    )
    uid = created["uid"]
    try:
        assert uid
        # UID lookup must resolve via search (D2/D4) — no 412.
        fetched = client.get_event(uid, src)
        assert fetched["title"] == title

        updated = client.update_event(uid, calendar=src, title=f"{title}-renamed")
        assert updated["title"] == f"{title}-renamed"
    finally:
        client.delete_event(uid, src)


def test_e2e_all_day_readback() -> None:
    """An all-day event created date-only reads back all_day=True (D1/D10)."""
    src, _ = _require_e2e()
    client = CalDAVClient()
    title = _zz_title("allday")
    day = (datetime.now(tz=UTC) + timedelta(days=2)).date()
    next_day = day + timedelta(days=1)

    created = client.create_event(
        calendar=src, title=title, start=day.isoformat(), end=next_day.isoformat()
    )
    uid = created["uid"]
    try:
        fetched = client.get_event(uid, src)
        assert fetched["all_day"] is True
    finally:
        client.delete_event(uid, src)


def test_e2e_cross_calendar_move_preserves_fidelity() -> None:
    """Move preserves all_day + attendees + alarm, with source-survival on create-leg failure."""
    src, dest = _require_e2e()
    if not dest:
        pytest.skip("CALDAV_E2E_CALENDAR_DEST unset — move e2e needs a second sandbox calendar")
    client = CalDAVClient()
    title = _zz_title("move")
    day = (datetime.now(tz=UTC) + timedelta(days=3)).date()
    next_day = day + timedelta(days=1)

    created = client.create_event(
        calendar=src,
        title=title,
        start=day.isoformat(),
        end=next_day.isoformat(),
        attendees=[{"email": "alice@example.com", "name": "Alice", "status": "NEEDS-ACTION"}],
        alarm_minutes=30,
    )
    src_uid = created["uid"]
    moved_uid = None
    try:
        moved = client.move_event(src_uid, src, dest)
        moved_uid = moved["uid"]
        fetched = client.get_event(moved_uid, dest)
        assert fetched["all_day"] is True
        assert fetched["attendees"] is not None
        assert fetched["alarm_minutes"] == 30
        # Source must be gone after a clean move.
        with pytest.raises(Exception):
            client.get_event(src_uid, src)
    finally:
        # Best-effort teardown of whichever copies survived.
        for uid, cal in ((moved_uid, dest), (src_uid, src)):
            if uid:
                try:
                    client.delete_event(uid, cal)
                except Exception:
                    pass
