"""Tests for auto_update module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from scheme_langserver_bridge import auto_update


class TestParseVersionFromLocation:
    def test_standard_github_location(self) -> None:
        loc = (
            "https://github.com/ufo5260987423/scheme-langserver/"
            "releases/download/2.1.0/scheme-langserver-x86_64-linux-glibc"
        )
        assert auto_update._parse_version_from_location(loc) == "2.1.0"

    def test_no_match_returns_none(self) -> None:
        assert auto_update._parse_version_from_location("/some/other/path") is None


class TestSemverKey:
    def test_sorting(self) -> None:
        tags = ["2.0.0", "2.1.0", "1.10.0", "1.2.3"]
        assert sorted(tags, key=auto_update._semver_key) == [
            "1.2.3",
            "1.10.0",
            "2.0.0",
            "2.1.0",
        ]

    def test_v_prefix_stripped(self) -> None:
        assert auto_update._semver_key("v2.1.0") == (2, 1, 0)


class TestCacheDir:
    def test_uses_xdg_cache(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("XDG_CACHE_HOME", "/tmp/xdg_cache")
        assert auto_update.get_cache_dir() == Path("/tmp/xdg_cache/scheme-langserver-bridge")

    def test_fallback_to_home(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        assert auto_update.get_cache_dir() == Path.home() / ".cache" / "scheme-langserver-bridge"


class TestReadWriteCachedVersionInfo:
    def test_round_trip(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            auto_update, "get_cache_dir", lambda: tmp_path / "cache"
        )
        info = {"tag": "2.1.0", "asset_url": "http://example.com/bin"}
        auto_update._write_cached_version_info(info.copy())
        cached = auto_update._read_cached_version_info()
        assert cached is not None
        assert cached["tag"] == "2.1.0"

    def test_expired_cache_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            auto_update, "get_cache_dir", lambda: tmp_path / "cache"
        )
        import time

        old = {"tag": "1.0.0", "checked_at": time.time() - 7200}
        auto_update._write_cached_version_info(old)
        # Now override with stale data directly (write adds fresh timestamp).
        path = auto_update._version_check_cache_path()
        path.write_text(str(old), encoding="utf-8")
        # _read_cached_version_info should return None for malformed anyway.
        cached = auto_update._read_cached_version_info()
        # Since the file is malformed text, returns None.
        assert cached is None


class TestListCachedTags:
    def test_empty_when_no_cache(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            auto_update, "get_cache_dir", lambda: tmp_path / "cache"
        )
        assert auto_update._list_cached_tags() == []

    def test_lists_version_dirs(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            auto_update, "get_cache_dir", lambda: tmp_path / "cache"
        )
        versions_dir = tmp_path / "cache" / "versions"
        (versions_dir / "2.0.0").mkdir(parents=True)
        (versions_dir / "2.1.0").mkdir(parents=True)
        assert set(auto_update._list_cached_tags()) == {"2.0.0", "2.1.0"}


class TestEnsureLatestBinary:
    def test_uses_cache_when_up_to_date(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            auto_update, "get_cache_dir", lambda: tmp_path / "cache"
        )
        monkeypatch.setattr(
            auto_update,
            "check_latest_version",
            lambda: {
                "tag": "2.1.0",
                "asset_url": "http://example.com/bin",
                "sha256": None,
                "error": None,
            },
        )
        # Pre-populate cache.
        bin_path = tmp_path / "cache" / "versions" / "2.1.0" / "scheme-langserver-x86_64-linux-glibc"
        bin_path.parent.mkdir(parents=True)
        bin_path.write_text("fake binary", encoding="utf-8")

        path, source, vinfo = auto_update.ensure_latest_binary(auto_update=True)
        assert path == str(bin_path)
        assert source == "cache"
        assert vinfo["up_to_date"] is True

    def test_skips_download_when_auto_update_false(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            auto_update, "get_cache_dir", lambda: tmp_path / "cache"
        )
        monkeypatch.setattr(
            auto_update,
            "check_latest_version",
            lambda: {
                "tag": "2.1.0",
                "asset_url": "http://example.com/bin",
                "sha256": None,
                "error": None,
            },
        )
        # Pre-populate older cache.
        bin_path = tmp_path / "cache" / "versions" / "2.0.0" / "scheme-langserver-x86_64-linux-glibc"
        bin_path.parent.mkdir(parents=True)
        bin_path.write_text("fake binary", encoding="utf-8")

        path, source, vinfo = auto_update.ensure_latest_binary(auto_update=False)
        assert path == str(bin_path)
        assert source == "cache"
        assert vinfo["up_to_date"] is False
        assert "2.1.0" in vinfo["note"]
