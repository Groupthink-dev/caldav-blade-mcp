"""Tests for models and configuration."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from caldav_blade_mcp.models import (
    is_write_enabled,
    parse_providers,
    require_write,
)


class TestWriteGate:
    def test_write_disabled_by_default(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            assert is_write_enabled() is False
            assert require_write() is not None

    def test_write_enabled(self) -> None:
        with patch.dict("os.environ", {"CALDAV_WRITE_ENABLED": "true"}):
            assert is_write_enabled() is True
            assert require_write() is None

    def test_write_enabled_case_insensitive(self) -> None:
        with patch.dict("os.environ", {"CALDAV_WRITE_ENABLED": "True"}):
            assert is_write_enabled() is True

    def test_write_disabled_explicit(self) -> None:
        with patch.dict("os.environ", {"CALDAV_WRITE_ENABLED": "false"}):
            assert is_write_enabled() is False


class TestParseProviders:
    def test_single_provider_mode(self) -> None:
        env = {
            "CALDAV_URL": "https://caldav.example.com/",
            "CALDAV_USERNAME": "user@example.com",
            "CALDAV_PASSWORD": "secret",
        }
        with patch.dict("os.environ", env, clear=True):
            providers = parse_providers()
            assert len(providers) == 1
            assert providers[0].name == "default"
            assert providers[0].url == "https://caldav.example.com/"

    def test_multi_provider_mode(self) -> None:
        env = {
            "CALDAV_PROVIDERS": "fastmail,icloud",
            "CALDAV_FASTMAIL_URL": "https://fm.example.com/",
            "CALDAV_FASTMAIL_USERNAME": "user@fm.com",
            "CALDAV_FASTMAIL_PASSWORD": "fm-pass",
            "CALDAV_ICLOUD_URL": "https://ic.example.com/",
            "CALDAV_ICLOUD_USERNAME": "user@ic.com",
            "CALDAV_ICLOUD_PASSWORD": "ic-pass",
        }
        with patch.dict("os.environ", env, clear=True):
            providers = parse_providers()
            assert len(providers) == 2
            assert providers[0].name == "fastmail"
            assert providers[1].name == "icloud"

    def test_missing_single_provider_raises(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(ValueError, match="credentials not configured"):
                parse_providers()

    def test_multi_provider_skips_incomplete(self) -> None:
        env = {
            "CALDAV_PROVIDERS": "fastmail,icloud",
            "CALDAV_FASTMAIL_URL": "https://fm.example.com/",
            "CALDAV_FASTMAIL_USERNAME": "user@fm.com",
            "CALDAV_FASTMAIL_PASSWORD": "fm-pass",
            # icloud is incomplete — missing URL
            "CALDAV_ICLOUD_USERNAME": "user@ic.com",
            "CALDAV_ICLOUD_PASSWORD": "ic-pass",
        }
        with patch.dict("os.environ", env, clear=True):
            providers = parse_providers()
            assert len(providers) == 1
            assert providers[0].name == "fastmail"

    def test_all_providers_incomplete_raises(self) -> None:
        env = {
            "CALDAV_PROVIDERS": "broken",
            "CALDAV_BROKEN_USERNAME": "user",
        }
        with patch.dict("os.environ", env, clear=True):
            with pytest.raises(ValueError, match="no providers configured"):
                parse_providers()
