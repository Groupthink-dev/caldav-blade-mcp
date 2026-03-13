# caldav-blade-mcp

A precision CalDAV MCP server that gives AI agents structured access to calendar data. Built for the [Model Context Protocol](https://modelcontextprotocol.io) with token efficiency as a first-class design goal.

## Why another calendar MCP?

Most calendar integrations dump entire iCalendar blobs into the context window. A single week view can burn 3,000+ tokens before the model even starts reasoning. caldav-blade-mcp takes a different approach:

- **Compact output** — pipe-delimited, one line per event, null fields omitted. A day's events in ~200 tokens.
- **Batch operations** — `cal_events_batch` fetches N calendars in one tool call. One call instead of eleven for a family digest.
- **Purpose-built views** — `cal_today`, `cal_week`, `cal_freebusy` give you exactly what you need with zero configuration.
- **Write-gated** — read by default, writes require explicit opt-in. `cal_delete` has an additional `confirm=true` safety gate.

## Quick start

```bash
# Install
uv pip install -e .

# Configure (single provider)
export CALDAV_URL="https://caldav.fastmail.com/dav/calendars/user/you@fastmail.com"
export CALDAV_USERNAME="you@fastmail.com"
export CALDAV_PASSWORD="app-specific-password"

# Run
caldav-blade-mcp
```

## 13 tools, 3 categories

### Read (9 tools)
| Tool | Purpose | Token cost |
|------|---------|------------|
| `cal_info` | Health check — providers, connection, write gate | ~50 |
| `cal_calendars` | List all calendars (name, UID, provider) | ~20/cal |
| `cal_events` | Events from one calendar in date range | ~30/event |
| `cal_events_batch` | Events from N calendars in one call | ~30/event |
| `cal_event` | Full detail by UID (attendees, recurrence) | ~100 |
| `cal_search` | Search by text, attendee, location | ~30/event |
| `cal_today` | Today's events across all calendars | ~30/event |
| `cal_week` | This week's events across all calendars | ~30/event |
| `cal_freebusy` | Busy periods only — cheapest availability check | ~15/period |

### Write (4 tools, gated)
| Tool | Purpose |
|------|---------|
| `cal_create` | Create event with all fields (description, location, recurrence, attendees, alarm) |
| `cal_update` | Partial update — only changed fields, auto-increments SEQUENCE |
| `cal_delete` | Delete by UID — requires `confirm=true` |
| `cal_move` | Move event between calendars |

### Output format

```
── Work ──
08:30-09:30 | Standup | uid=abc123
14:00-15:00 | Design review @ Level 3 | loc=Level 3 | uid=def456

── Personal ──
All day | School holidays | uid=ghi789
18:30-19:30 | Swim squad | uid=jkl012
```

## Multi-provider support

Run against multiple CalDAV servers simultaneously — useful when calendars span Fastmail, iCloud, Google, or self-hosted (Radicale, Baïkal, Nextcloud).

```bash
export CALDAV_PROVIDERS="fastmail,icloud"
export CALDAV_FASTMAIL_URL="https://caldav.fastmail.com/dav/calendars/user/..."
export CALDAV_FASTMAIL_USERNAME="you@fastmail.com"
export CALDAV_FASTMAIL_PASSWORD="fm-app-password"
export CALDAV_ICLOUD_URL="https://caldav.icloud.com/"
export CALDAV_ICLOUD_USERNAME="you@icloud.com"
export CALDAV_ICLOUD_PASSWORD="app-specific-password"
```

Single-provider mode (the `CALDAV_URL` / `CALDAV_USERNAME` / `CALDAV_PASSWORD` env vars) remains fully supported for simple setups.

## Security model

| Layer | Mechanism |
|-------|-----------|
| **Write gate** | `CALDAV_WRITE_ENABLED=true` required for any mutation |
| **Delete safety** | `cal_delete` additionally requires `confirm=true` |
| **Credential scrubbing** | Passwords and server URLs stripped from all error output |
| **Bearer auth** | Optional `CALDAV_MCP_API_TOKEN` for HTTP transport |
| **No caching** | Credentials read from env at startup, never persisted |

## Claude Code integration

Add to your MCP config (e.g. `claude.nix` or `settings.json`):

```json
{
  "mcpServers": {
    "calendar-piers": {
      "type": "stdio",
      "command": "uv",
      "args": ["--directory", "~/src/caldav-blade-mcp", "run", "caldav-blade-mcp"],
      "env": {
        "CALDAV_URL": "...",
        "CALDAV_USERNAME": "...",
        "CALDAV_PASSWORD": "...",
        "CALDAV_WRITE_ENABLED": "false"
      }
    }
  }
}
```

A bundled `SKILL.md` teaches Claude the optimal tool-calling patterns — batch over individual, freebusy over full event scans, convenience views for common queries.

## Development

```bash
make install-dev    # Install with dev + test dependencies
make test           # Unit tests (mocked caldav, no server needed)
make check          # Lint + format + type-check
make run            # Start MCP server (stdio)
```

### Architecture

```
src/caldav_blade_mcp/
├── server.py       — FastMCP 2.0 server, 13 @mcp.tool decorators
├── client.py       — CalDAVClient with multi-provider, typed exceptions
├── formatters.py   — Token-efficient output (pipe-delimited, null omission)
├── models.py       — Provider config, write-gate, constants
└── auth.py         — Bearer token middleware for HTTP transport
```

Built with [FastMCP 2.0](https://github.com/jlowin/fastmcp), [`caldav`](https://github.com/python-caldav/caldav), and [`python-dateutil`](https://github.com/dateutil/dateutil).

## License

MIT
