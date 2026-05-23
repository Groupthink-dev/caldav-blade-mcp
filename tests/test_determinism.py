"""DD-338 Phase B.2 — multi-provider determinism harness.

Acceptance gates per spec § 7 / architect amendment § Approval:

1. ``cal_info`` single-record ``stable`` declaration → N=5 byte-equal output
   against fixed mock provider state.
2. ``cal_calendars``, ``cal_events_batch``, ``cal_search``, ``cal_today``,
   ``cal_week`` honest-unsorted declaration → N=5 **set-equality**
   (NOT byte-equality) of returned records across invocations.
3. ``cal_calendars`` partial-tolerance softening (OQ-3 RATIFIED YES) →
   one failing provider does NOT kill the listing for healthy providers;
   error provenance surfaces in-band.

The set-equality contract for ``unsorted`` mirrors the cf_d1_query
honest-degraded precedent from B.1.b — the blade declares set semantics
and explicitly delegates canonical ordering to the assembler.

NOTE: defensive sorts inside the blade are intentionally absent
(OQ-2 RATIFIED NO). Adding an internal sort under an ``unsorted``
declaration would be fake-compliance — exactly what cf_d1_query rejects.
"""

from __future__ import annotations

import json
import re
from typing import Any
from unittest.mock import MagicMock, patch

from caldav_blade_mcp.client import CalDAVClient
from caldav_blade_mcp.models import ProviderConfig
from caldav_blade_mcp.server import (
    cal_calendars,
    cal_events,
    cal_events_batch,
    cal_freebusy,
    cal_info,
    cal_search,
    cal_today,
    cal_week,
)
from tests.conftest import make_calendar_obj, make_event_obj, make_vevent

# ---------------------------------------------------------------------------
# cal_info — single-record stable (N=5 byte-equal)
# ---------------------------------------------------------------------------


class TestCalInfoStable:
    """cal_info is single-record; ``stable`` contract = byte-identical N=5."""

    async def test_cal_info_byte_equal_across_5_invocations(self) -> None:
        """DD-338 B.2 — cal_info returns byte-identical text across N=5 invocations."""
        client = MagicMock()
        client.info.return_value = {
            "providers": [
                {"name": "fastmail", "status": "connected", "calendars": 3},
                {"name": "icloud", "status": "connected", "calendars": 7},
            ],
            "total_calendars": 10,
            "write_enabled": False,
        }
        with patch("caldav_blade_mcp.server._get_client", return_value=client):
            outputs = [await cal_info() for _ in range(5)]
        assert len(outputs) == 5
        # All 5 invocations byte-identical (stable contract)
        assert all(o == outputs[0] for o in outputs), (
            "cal_info MUST be byte-identical across N=5 invocations (stable contract)"
        )


# ---------------------------------------------------------------------------
# Helpers — set extraction for honest-unsorted contract
# ---------------------------------------------------------------------------


def _extract_lines_set(formatted: str) -> frozenset[str]:
    """Extract the set of non-empty content lines from formatted output.

    For honest-unsorted tools we assert SET equality — same lines may appear
    in different order across invocations; the contract is that the SET of
    records is invariant.

    DD-338 Phase C Wave 2 — skips the trailing ``_meta:`` JSON line (the
    envelope carries non-deterministic ``latency_ms``).
    """
    return frozenset(
        line
        for line in formatted.splitlines()
        if line.strip() and not line.startswith("##") and not line.startswith("_meta:")
    )


# ---------------------------------------------------------------------------
# cal_calendars — unsorted (set-equality)
# ---------------------------------------------------------------------------


class TestCalCalendarsUnsorted:
    """cal_calendars is ``unsorted``; set-equality contract."""

    async def test_cal_calendars_set_stable_across_5_invocations(self) -> None:
        """DD-338 B.2 — cal_calendars returns the same SET of records across N=5 invocations."""
        client = MagicMock()
        client.list_calendars.return_value = [
            {"name": "Work", "uid": "work-1", "provider": "fastmail"},
            {"name": "Personal", "uid": "pers-1", "provider": "fastmail"},
            {"name": "Family", "uid": "fam-1", "provider": "icloud"},
        ]
        with patch("caldav_blade_mcp.server._get_client", return_value=client):
            sets = [_extract_lines_set(await cal_calendars()) for _ in range(5)]
        assert len(sets) == 5
        assert all(s == sets[0] for s in sets), (
            "cal_calendars MUST return the same SET of records across N=5 invocations (unsorted contract)"
        )

    async def test_cal_calendars_partial_provider_failure_tolerated(self) -> None:
        """DD-338 B.2 OQ-3 — one provider failing does NOT kill the listing.

        Per architect amendment OQ-3 RATIFIED YES: a slow/down iCloud must not
        block Fastmail's calendars from surfacing. Error provenance lands as
        an in-band row.
        """
        # Build a real CalDAVClient with a healthy + failing provider.
        with patch("caldav_blade_mcp.client.DAVClient") as mock_dav_cls:
            healthy_cal = make_calendar_obj("Fastmail-Work", "fm-work-id")
            healthy_principal = MagicMock()
            healthy_principal.calendars.return_value = [healthy_cal]

            failing_principal = MagicMock()
            failing_principal.calendars.side_effect = Exception("Connection timeout to iCloud")

            mock_dav_cls.return_value.principal.side_effect = [healthy_principal, failing_principal]

            providers = [
                ProviderConfig(name="fastmail", url="https://fm.example.com", username="u1", password="p1"),
                ProviderConfig(name="icloud", url="https://ic.example.com", username="u2", password="p2"),
            ]
            client = CalDAVClient(providers=providers)
            result = client.list_calendars()

        # Fastmail calendar surfaces normally
        fastmail_rows = [r for r in result if r.get("provider") == "fastmail" and r.get("name") == "Fastmail-Work"]
        assert len(fastmail_rows) == 1, "Fastmail calendar must surface despite iCloud failure"

        # iCloud error surfaces as an in-band error row
        icloud_errors = [r for r in result if r.get("provider") == "icloud" and "error" in r]
        assert len(icloud_errors) == 1, "iCloud failure must surface as in-band error row"
        assert "Connection timeout" in icloud_errors[0]["error"]

    async def test_cal_calendars_formatter_renders_error_row(self) -> None:
        """DD-338 B.2 OQ-4 — formatter renders the error-row variant with provenance prefix."""
        from caldav_blade_mcp.formatters import format_calendar_list

        rows = [
            {"name": "Work", "uid": "work-1", "provider": "fastmail"},
            {"provider": "icloud", "error": "Connection timeout"},
        ]
        rendered = format_calendar_list(rows)
        # Healthy row renders normally
        assert "Work" in rendered
        assert "uid=work-1" in rendered
        # Error row renders with warning prefix + provider + error + tag
        assert "⚠ icloud" in rendered
        assert "Connection timeout" in rendered
        assert "no calendars listed" in rendered


# ---------------------------------------------------------------------------
# cal_events_batch / cal_today / cal_week — unsorted set-equality
# ---------------------------------------------------------------------------


def _events_grouped_set(formatted: str) -> frozenset[tuple[str, frozenset[str]]]:
    """Extract the SET of (calendar_name, frozenset(event_lines)) from grouped output.

    For unsorted tools, both the outer calendar order AND inner event order
    are NOT contractually stable — only the set of records is.

    DD-338 Phase C Wave 2 — skips the trailing ``_meta:`` JSON line.
    """
    groups: dict[str, list[str]] = {}
    current_cal: str | None = None
    for line in formatted.splitlines():
        if line.startswith("_meta:"):
            # Tail envelope — done with grouped content.
            break
        if line.startswith("## "):
            # "## Work (3 events)" → "Work"
            cal_part = line[3:].rsplit(" (", 1)[0]
            current_cal = cal_part
            groups[current_cal] = []
        elif current_cal and line.strip() and not line.startswith("("):
            groups[current_cal].append(line)
    return frozenset((cal, frozenset(events)) for cal, events in groups.items())


class TestCalEventsBatchUnsorted:
    """cal_events_batch is ``unsorted``; set-equality contract."""

    async def test_cal_events_batch_set_stable_across_5_invocations(self) -> None:
        client = MagicMock()
        client.get_events_batch.return_value = {
            "Work": [
                {
                    "uid": "ev-1",
                    "title": "Standup",
                    "start": "2026-05-23T09:00:00+00:00",
                    "end": "2026-05-23T09:30:00+00:00",
                    "all_day": False,
                },
                {
                    "uid": "ev-2",
                    "title": "Review",
                    "start": "2026-05-23T14:00:00+00:00",
                    "end": "2026-05-23T15:00:00+00:00",
                    "all_day": False,
                },
            ],
            "Personal": [
                {
                    "uid": "ev-3",
                    "title": "Gym",
                    "start": "2026-05-23T17:00:00+00:00",
                    "end": "2026-05-23T18:00:00+00:00",
                    "all_day": False,
                },
            ],
        }
        with patch("caldav_blade_mcp.server._get_client", return_value=client):
            sets = [
                _events_grouped_set(
                    await cal_events_batch(
                        ["Work", "Personal"], "2026-05-23T00:00:00+00:00", "2026-05-24T00:00:00+00:00"
                    )
                )
                for _ in range(5)
            ]
        assert all(s == sets[0] for s in sets), (
            "cal_events_batch MUST return the same SET of (calendar, events) across N=5 invocations"
        )


class TestCalTodayUnsorted:
    """cal_today is ``unsorted``; set-equality contract."""

    async def test_cal_today_set_stable_across_5_invocations(self) -> None:
        client = MagicMock()
        client.get_today.return_value = {
            "Work": [
                {
                    "uid": "ev-1",
                    "title": "Morning sync",
                    "start": "2026-05-23T09:00:00+00:00",
                    "end": "2026-05-23T09:30:00+00:00",
                    "all_day": False,
                },
            ],
            "Family": [
                {
                    "uid": "ev-2",
                    "title": "School pickup",
                    "start": "2026-05-23T15:00:00+00:00",
                    "end": "2026-05-23T15:30:00+00:00",
                    "all_day": False,
                },
            ],
        }
        with patch("caldav_blade_mcp.server._get_client", return_value=client):
            sets = [_events_grouped_set(await cal_today()) for _ in range(5)]
        assert all(s == sets[0] for s in sets), (
            "cal_today MUST return the same SET of (calendar, events) across N=5 invocations"
        )


class TestCalWeekUnsorted:
    """cal_week is ``unsorted``; set-equality contract."""

    async def test_cal_week_set_stable_across_5_invocations(self) -> None:
        client = MagicMock()
        client.get_week.return_value = {
            "Work": [
                {
                    "uid": "ev-1",
                    "title": "Mon standup",
                    "start": "2026-05-25T09:00:00+00:00",
                    "end": "2026-05-25T09:30:00+00:00",
                    "all_day": False,
                },
                {
                    "uid": "ev-2",
                    "title": "Wed review",
                    "start": "2026-05-27T14:00:00+00:00",
                    "end": "2026-05-27T15:00:00+00:00",
                    "all_day": False,
                },
            ],
        }
        with patch("caldav_blade_mcp.server._get_client", return_value=client):
            sets = [_events_grouped_set(await cal_week()) for _ in range(5)]
        assert all(s == sets[0] for s in sets), (
            "cal_week MUST return the same SET of (calendar, events) across N=5 invocations"
        )


# ---------------------------------------------------------------------------
# cal_search — unsorted set-equality (UID set)
# ---------------------------------------------------------------------------


def _search_uid_set(formatted: str) -> frozenset[str]:
    """Extract the set of UIDs from cal_search output (uid=... fields)."""
    import re

    return frozenset(re.findall(r"uid=([^\s|]+)", formatted))


class TestCalSearchUnsorted:
    """cal_search is ``unsorted``; set-equality contract.

    NO defensive sort applied per architect amendment OQ-2 RATIFIED NO.
    """

    async def test_cal_search_set_stable_across_5_invocations(self) -> None:
        client = MagicMock()
        client.search_events.return_value = [
            {
                "uid": "ev-alpha",
                "title": "Team standup",
                "start": "2026-05-23T09:00:00+00:00",
                "end": "2026-05-23T09:30:00+00:00",
                "all_day": False,
            },
            {
                "uid": "ev-beta",
                "title": "Standup retro",
                "start": "2026-05-24T10:00:00+00:00",
                "end": "2026-05-24T11:00:00+00:00",
                "all_day": False,
            },
            {
                "uid": "ev-gamma",
                "title": "Standup with PM",
                "start": "2026-05-23T15:00:00+00:00",
                "end": "2026-05-23T15:30:00+00:00",
                "all_day": False,
            },
        ]
        with patch("caldav_blade_mcp.server._get_client", return_value=client):
            sets = [_search_uid_set(await cal_search(query="standup")) for _ in range(5)]
        assert all(s == sets[0] for s in sets), (
            "cal_search MUST return the same SET of UIDs across N=5 invocations (unsorted contract)"
        )
        # All 3 events match the query
        assert sets[0] == frozenset({"ev-alpha", "ev-beta", "ev-gamma"})


# ---------------------------------------------------------------------------
# OQ-2 negative test: no defensive sort under unsorted declaration
# ---------------------------------------------------------------------------


class TestNoDefensiveSortUnderUnsorted:
    """DD-338 B.2 OQ-2 RATIFIED NO — no defensive sort inside the blade.

    Declaring ``unsorted`` while internally sorting would be fake-compliance —
    masks the cross-provider set-semantics the assembler legitimately needs
    to see. This regression test asserts ``search_events`` does NOT sort its
    output (the events come back in the order the underlying caldav library
    + the per-calendar iteration produced them).
    """

    @patch("caldav_blade_mcp.client.DAVClient")
    def test_search_events_does_not_sort_internally(self, mock_dav_cls: MagicMock) -> None:
        # Three events in deliberately non-canonical (start, uid) order
        # If a defensive sort were applied, output would re-order to:
        #   c (09:00) < b (10:00) < a (11:00)
        # Without defensive sort, output preserves cal.search() return order.
        from datetime import UTC, datetime

        vevent_a = make_vevent(uid="zzz-a", summary="standup", dtstart=datetime(2026, 5, 23, 11, 0, tzinfo=UTC))
        vevent_b = make_vevent(uid="mmm-b", summary="standup", dtstart=datetime(2026, 5, 23, 10, 0, tzinfo=UTC))
        vevent_c = make_vevent(uid="aaa-c", summary="standup", dtstart=datetime(2026, 5, 23, 9, 0, tzinfo=UTC))

        cal = make_calendar_obj("All", "all-id")
        # Deliberately return in non-canonical order: a, b, c
        cal.search.return_value = [make_event_obj(vevent_a), make_event_obj(vevent_b), make_event_obj(vevent_c)]

        mock_principal = MagicMock()
        mock_principal.calendars.return_value = [cal]
        mock_dav_cls.return_value.principal.return_value = mock_principal

        provider = ProviderConfig(name="test", url="https://example.com", username="u", password="p")
        client = CalDAVClient(providers=[provider])
        result = client.search_events(query="standup")

        # Blade preserves cal.search() return order (no defensive sort)
        uids = [r["uid"] for r in result]
        assert uids == ["zzz-a", "mmm-b", "aaa-c"], (
            "cal_search must NOT apply a defensive (start, uid) sort — OQ-2 RATIFIED NO. "
            f"Found order {uids!r}; expected ['zzz-a', 'mmm-b', 'aaa-c'] (cal.search() order preserved)."
        )


# ---------------------------------------------------------------------------
# DD-338 Phase C Wave 2 — _meta envelope per-tool + N=3 byte-equal determinism
# ---------------------------------------------------------------------------


_META_RE = re.compile(r"\n\n_meta: (\{.*\})$", re.DOTALL)


def _split_meta(text: str) -> tuple[str, dict[str, Any]]:
    m = _META_RE.search(text)
    assert m is not None, f"_meta envelope not found in:\n{text!r}"
    return text[: m.start()], json.loads(m.group(1))


def _strip_latency(text: str) -> str:
    payload, meta = _split_meta(text)
    meta.pop("latency_ms", None)
    return payload + "\n\n_meta: " + json.dumps(meta, sort_keys=True)


def _check_meta_shape(meta: dict[str, Any]) -> None:
    assert isinstance(meta["matched_total"], int)
    assert isinstance(meta["returned"], int)
    assert isinstance(meta["filtered_by"], list)
    assert isinstance(meta["latency_ms"], int)


class TestCalCalendarsMeta:
    async def test_meta_envelope_shape(self) -> None:
        client = MagicMock()
        client.list_calendars.return_value = [
            {"name": "Work", "uid": "w-1", "provider": "fastmail"},
            {"name": "Personal", "uid": "p-1", "provider": "fastmail"},
        ]
        with patch("caldav_blade_mcp.server._get_client", return_value=client):
            _payload, meta = _split_meta(await cal_calendars())
        _check_meta_shape(meta)
        assert meta["matched_total"] == 2
        assert meta["returned"] == 2
        assert meta["filtered_by"] == []

    async def test_meta_error_notes_per_provider_failure(self) -> None:
        """OQ-6: per-provider failures surface as structured error_notes rows."""
        client = MagicMock()
        client.list_calendars.return_value = [
            {"name": "Work", "uid": "w-1", "provider": "fastmail"},
            {"provider": "icloud", "error": "Connection timeout"},
        ]
        with patch("caldav_blade_mcp.server._get_client", return_value=client):
            payload, meta = _split_meta(await cal_calendars())
        # Real-row count, not including the error row
        assert meta["matched_total"] == 1
        assert meta["returned"] == 1
        assert "error_notes" in meta
        assert any("icloud" in n and "Connection timeout" in n for n in meta["error_notes"])
        # In-band ⚠ row still present in payload
        assert "⚠ icloud" in payload


class TestCalEventsMeta:
    async def test_meta_envelope_shape(self) -> None:
        client = MagicMock()
        client.get_events.return_value = [
            {
                "uid": "ev-1",
                "title": "A",
                "start": "2026-03-13T09:00:00+00:00",
                "end": "2026-03-13T09:30:00+00:00",
                "all_day": False,
            },
        ]
        with patch("caldav_blade_mcp.server._get_client", return_value=client):
            _payload, meta = _split_meta(
                await cal_events("Work", "2026-03-13T00:00:00+00:00", "2026-03-14T00:00:00+00:00")
            )
        _check_meta_shape(meta)
        assert meta["returned"] == 1
        assert "calendar=Work" in meta["filtered_by"]
        assert any("time_range=" in f for f in meta["filtered_by"])


class TestCalEventsBatchMeta:
    async def test_meta_envelope_shape(self) -> None:
        client = MagicMock()
        client.get_events_batch.return_value = {
            "Work": [
                {
                    "uid": "ev-1",
                    "title": "A",
                    "start": "2026-03-13T10:00:00+00:00",
                    "end": "2026-03-13T11:00:00+00:00",
                    "all_day": False,
                }
            ],
            "Personal": [],
        }
        with patch("caldav_blade_mcp.server._get_client", return_value=client):
            _payload, meta = _split_meta(
                await cal_events_batch(
                    ["Work", "Personal"],
                    "2026-03-13T00:00:00+00:00",
                    "2026-03-14T00:00:00+00:00",
                )
            )
        _check_meta_shape(meta)
        assert meta["returned"] == 1
        assert "calendars=2" in meta["filtered_by"]


class TestCalSearchMeta:
    async def test_meta_envelope_shape(self) -> None:
        client = MagicMock()
        client.search_events.return_value = [
            {
                "uid": "ev-1",
                "title": "dentist",
                "start": "2026-03-13T14:00:00+00:00",
                "end": "2026-03-13T15:00:00+00:00",
                "all_day": False,
            },
        ]
        with patch("caldav_blade_mcp.server._get_client", return_value=client):
            _payload, meta = _split_meta(await cal_search(query="dentist", attendee="me@x.com"))
        _check_meta_shape(meta)
        assert "query=dentist" in meta["filtered_by"]
        assert "attendee=me@x.com" in meta["filtered_by"]


class TestCalTodayMeta:
    async def test_meta_envelope_shape(self) -> None:
        client = MagicMock()
        client.get_today.return_value = {
            "Work": [
                {
                    "uid": "ev-1",
                    "title": "x",
                    "start": "2026-03-13T09:00:00+00:00",
                    "end": "2026-03-13T09:30:00+00:00",
                    "all_day": False,
                }
            ],
        }
        with patch("caldav_blade_mcp.server._get_client", return_value=client):
            _payload, meta = _split_meta(await cal_today())
        _check_meta_shape(meta)
        assert meta["returned"] == 1
        assert any(f.startswith("date=") for f in meta["filtered_by"])


class TestCalWeekMeta:
    async def test_meta_envelope_shape(self) -> None:
        client = MagicMock()
        client.get_week.return_value = {
            "Work": [
                {
                    "uid": "ev-1",
                    "title": "x",
                    "start": "2026-03-13T09:00:00+00:00",
                    "end": "2026-03-13T09:30:00+00:00",
                    "all_day": False,
                }
            ],
        }
        with patch("caldav_blade_mcp.server._get_client", return_value=client):
            _payload, meta = _split_meta(await cal_week(start_monday=True))
        _check_meta_shape(meta)
        assert "start_monday=True" in meta["filtered_by"]
        assert "week=7d" in meta["filtered_by"]


class TestCalFreebusyMeta:
    async def test_meta_envelope_shape(self) -> None:
        client = MagicMock()
        client.freebusy.return_value = [
            {"start": "2026-03-13T09:00:00+00:00", "end": "2026-03-13T10:00:00+00:00"},
        ]
        with patch("caldav_blade_mcp.server._get_client", return_value=client):
            _payload, meta = _split_meta(
                await cal_freebusy("2026-03-13T00:00:00+00:00", "2026-03-14T00:00:00+00:00", calendar="Work")
            )
        _check_meta_shape(meta)
        assert "calendar=Work" in meta["filtered_by"]
        assert any("time_range=" in f for f in meta["filtered_by"])


# ---------------------------------------------------------------------------
# Helper-shape direct test for canonical ``stallari_mcp_helpers`` envelope
# (DD-338 Phase E.python — local ``_append_meta`` retired; the call-sites
# now invoke ``append_meta`` + ``meta_envelope`` from the canonical lib.)
# ---------------------------------------------------------------------------


class TestAppendMetaHelper:
    def test_helper_appends_envelope(self) -> None:
        from stallari_mcp_helpers import append_meta, meta_envelope

        meta = {
            "matched_total": 5,
            "returned": 3,
            "filtered_by": ["scope=work"],
            "latency_ms": 42,
        }
        body = "line1\nline2"
        out = append_meta(body, meta_envelope(**meta))
        assert out.startswith("line1\nline2\n\n_meta: ")
        parsed = json.loads(out.split("_meta: ", 1)[1])
        # Canonical envelope always carries redactions=[] and next_cursor=null
        # in addition to the caller-supplied fields.
        assert parsed["matched_total"] == 5
        assert parsed["returned"] == 3
        assert parsed["filtered_by"] == ["scope=work"]
        assert parsed["latency_ms"] == 42
        assert parsed["redactions"] == []
        assert parsed["next_cursor"] is None

    def test_helper_passthrough_when_meta_none(self) -> None:
        # The blade's formatter contract: meta=None ⇒ body passes through
        # unchanged. With the canonical lib that is enforced at the
        # formatter call-site, not inside the helper.
        from caldav_blade_mcp.formatters import format_event_list

        body_out = format_event_list([], meta=None)
        assert body_out == "(no events)"


# ---------------------------------------------------------------------------
# DD-338 Phase C Wave 2 — N=3 byte-equal determinism per promoted tool
# ---------------------------------------------------------------------------


class TestCalDeterministicN3:
    async def test_cal_events_byte_equal_n3(self) -> None:
        client = MagicMock()
        client.get_events.return_value = [
            {
                "uid": "ev-1",
                "title": "A",
                "start": "2026-03-13T09:00:00+00:00",
                "end": "2026-03-13T09:30:00+00:00",
                "all_day": False,
            },
        ]
        outs: list[str] = []
        with patch("caldav_blade_mcp.server._get_client", return_value=client):
            for _ in range(3):
                outs.append(
                    _strip_latency(await cal_events("Work", "2026-03-13T00:00:00+00:00", "2026-03-14T00:00:00+00:00"))
                )
        assert all(o == outs[0] for o in outs)

    async def test_cal_freebusy_byte_equal_n3(self) -> None:
        client = MagicMock()
        client.freebusy.return_value = [
            {"start": "2026-03-13T09:00:00+00:00", "end": "2026-03-13T10:00:00+00:00"},
        ]
        outs: list[str] = []
        with patch("caldav_blade_mcp.server._get_client", return_value=client):
            for _ in range(3):
                outs.append(
                    _strip_latency(
                        await cal_freebusy(
                            "2026-03-13T00:00:00+00:00",
                            "2026-03-14T00:00:00+00:00",
                        )
                    )
                )
        assert all(o == outs[0] for o in outs)
