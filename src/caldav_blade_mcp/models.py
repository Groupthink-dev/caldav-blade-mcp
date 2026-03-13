"""Shared constants, types, and write-gate for CalDAV Blade MCP server."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Default limit for list operations (token efficiency)
DEFAULT_LIMIT = 50

# Maximum title/description lengths for input validation
MAX_TITLE_LENGTH = 500
MAX_DESCRIPTION_LENGTH = 5000


@dataclass
class ProviderConfig:
    """Configuration for a single CalDAV provider."""

    name: str
    url: str
    username: str
    password: str


def parse_providers() -> list[ProviderConfig]:
    """Parse CalDAV provider configuration from environment variables.

    Supports two modes:

    1. Multi-provider: ``CALDAV_PROVIDERS=fastmail,icloud`` with per-provider
       ``CALDAV_FASTMAIL_URL``, ``CALDAV_FASTMAIL_USERNAME``, ``CALDAV_FASTMAIL_PASSWORD``

    2. Single-provider (backward-compatible): ``CALDAV_URL``, ``CALDAV_USERNAME``,
       ``CALDAV_PASSWORD`` treated as provider "default".
    """
    providers_str = os.environ.get("CALDAV_PROVIDERS", "").strip()
    if providers_str:
        providers = []
        for name in providers_str.split(","):
            name = name.strip()
            prefix = f"CALDAV_{name.upper()}_"
            url = os.environ.get(f"{prefix}URL", "")
            username = os.environ.get(f"{prefix}USERNAME", "")
            password = os.environ.get(f"{prefix}PASSWORD", "")
            if not all([url, username, password]):
                logger.warning("Incomplete config for provider %s — skipping", name)
                continue
            providers.append(ProviderConfig(name=name, url=url, username=username, password=password))
        if not providers:
            raise ValueError("CALDAV_PROVIDERS set but no providers configured correctly")
        return providers

    # Backward-compatible single-provider mode
    url = os.environ.get("CALDAV_URL", "")
    username = os.environ.get("CALDAV_USERNAME", "")
    password = os.environ.get("CALDAV_PASSWORD", "")
    if not all([url, username, password]):
        raise ValueError("CalDAV credentials not configured. Set CALDAV_URL, CALDAV_USERNAME, CALDAV_PASSWORD")
    return [ProviderConfig(name="default", url=url, username=username, password=password)]


def is_write_enabled() -> bool:
    """Check if write operations are enabled via env var."""
    return os.environ.get("CALDAV_WRITE_ENABLED", "").lower() == "true"


def require_write() -> str | None:
    """Return an error message if writes are disabled, else None."""
    if not is_write_enabled():
        return "Error: Write operations are disabled. Set CALDAV_WRITE_ENABLED=true to enable."
    return None
