# Changelog

All notable changes to `caldav-blade-mcp` are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] - 2026-05-24

### Changed
- DD-338 Phase E.python: depend on `stallari-mcp-helpers>=0.1.0,<1.0.0`; deleted
  local `_meta`-envelope helper (`_append_meta` in `formatters.py`). Pure substrate
  swap — no behavioural change. Wire-shape: `_meta.filtered_by` now alphabetically
  sorted (caller-order preservation retired); JSON separators stay tight (already
  canonical pre-flip); `redactions: []` and `next_cursor: null` always emitted by
  the canonical builder. The empty-body case now joins with `\n\n` unconditionally
  (assembler regex `\n\n_meta: (\{.*\})$` still matches).
