# Changelog

All notable changes to `caldav-blade-mcp` are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.0] - 2026-06-07

### Fixed (DD-385 live-hardening — 10 of 12 audit defects)

Live-hardened against iCloud CalDAV; every fix came from a live failure the
mock suite passed straight through (DD-385 meta-lesson).

- **D2/D4 — UID lookup (blocking):** `_find_event` resolved events via
  `caldav.object_by_uid`, which iCloud answers with `412 Precondition Failed`
  — breaking every UID-addressed tool (`cal_event`/`cal_update`/`cal_delete`/
  `cal_move`). Now resolves via a `cal.search(event=True)` UID match (the proven
  iCloud read path), maps `DAVError`/412 through `_classify_error`.
- **D2 — non-fatal re-fetch (blocking):** `create_event` AND `update_event`
  re-fetched the event after the PUT landed; a re-fetch failure reported a
  *successful* write as a failure (duplicate/re-edit hazard). The re-fetch is
  now best-effort — a landed PUT is never reported as failed.
- **D9/D8/D10 — `cal_move` data loss (blocking):** `move_event` deleted the
  source *before* creating at the destination with no rollback — a failed create
  permanently destroyed the event. Rewritten create-at-dest → verify-landed →
  delete-source-last, with attendee/alarm/all-day fidelity and a partial-success
  signal. The irreversible leg can no longer precede the fallible one.
- **D7 — `cal_move` confirm gate (blocking):** `cal_move` reached the same
  `event.delete()` sink as `cal_delete` with no `confirm` gate. Added.
- **D1 — all-day events:** `create_event`/`update_event` coerced date-only input
  to midnight timed events; now construct `VALUE=DATE` all-day events (new
  `all_day` param + auto-detect).
- **D3 — dateless search (blocking):** `cal_search` always passed `expand=True`;
  iCloud rejects expand without a date range and the broad except swallowed it
  into a guaranteed false-empty. `expand` is now conditional on a date range.
- **D5 — connection poisoning (blocking):** an auth failure during lazy
  `connect()` left `_dav` set but `_principal` None, so every later call returned
  a misleading `AttributeError` and never retried. The cached handle is now
  assigned only after `principal()` succeeds; failure rolls back and re-surfaces
  the real error.
- **D6 — error classification:** write-method `caldav` exceptions now route
  through `_classify_error` so writes return clean errors like reads.

Deferred (tracked DD-385/DD-386): CONV-29 `_meta` on mutation tools + DD-280
`risk_class` (FastMCP framework-slot question). Live e2e (DD-382 pattern) added
under `tests/e2e/`, skipped by default.

## [0.3.0] - 2026-05-24

### Changed
- DD-338 Phase E.python: depend on `stallari-mcp-helpers>=0.1.0,<1.0.0`; deleted
  local `_meta`-envelope helper (`_append_meta` in `formatters.py`). Pure substrate
  swap — no behavioural change. Wire-shape: `_meta.filtered_by` now alphabetically
  sorted (caller-order preservation retired); JSON separators stay tight (already
  canonical pre-flip); `redactions: []` and `next_cursor: null` always emitted by
  the canonical builder. The empty-body case now joins with `\n\n` unconditionally
  (assembler regex `\n\n_meta: (\{.*\})$` still matches).
