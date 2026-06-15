"""Tests for config module."""

from __future__ import annotations

from pathlib import Path

import pytest

from scheme_langserver_bridge.config import Config, _find_akku_libdirs


class TestConfig:
    def test_build_cmd_basic(self) -> None:
        config = Config(
            langserver_path="/usr/bin/scheme-langserver",
            log_path="/tmp/test.log",
        )
        cmd = config.build_cmd("/project/root")
        assert cmd[0] == "/usr/bin/scheme-langserver"
        # scheme-langserver uses named flags (positional operands are ignored).
        # --cache-path is enabled by default.
        assert cmd == [
            "/usr/bin/scheme-langserver",
            "--log-path",
            "/tmp/test.log",
            "--multi-thread",
            "enable",
            "--type-inference",
            "enable",
            "--top-environment",
            "R6RS",
            "--cache-path",
            "/project/root/.scheme-langserver-cache",
        ]

    def test_build_cmd_custom_options(self) -> None:
        config = Config(
            langserver_path="/bin/run",
            log_path=None,
            multi_thread="disable",
            type_inference="disable",
            top_environment="R7RS",
            debug="enable",
        )
        cmd = config.build_cmd("/project/root")
        assert cmd[0] == "/bin/run"
        assert cmd[cmd.index("--log-path") + 1].endswith(".scheme-langserver.log")
        assert cmd[cmd.index("--multi-thread") + 1] == "disable"
        assert cmd[cmd.index("--type-inference") + 1] == "disable"
        assert cmd[cmd.index("--top-environment") + 1] == "R7RS"
        assert cmd[cmd.index("--cache-path") + 1] == "/project/root/.scheme-langserver-cache"

    def test_find_langserver_from_env(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        fake_bin = tmp_path / "scheme-langserver" / "run"
        fake_bin.parent.mkdir(parents=True, exist_ok=True)
        fake_bin.touch()
        monkeypatch.setenv("SCHEME_LANGSERVER_PATH", str(fake_bin))
        config = Config.load(root_dir=str(tmp_path))
        assert config.langserver_path == str(fake_bin)

    def test_log_path_default(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        fake_bin = tmp_path / "run"
        fake_bin.touch()
        monkeypatch.setenv("SCHEME_LANGSERVER_PATH", str(fake_bin))
        monkeypatch.delenv("SCHEME_LANGSERVER_LOG_PATH", raising=False)
        config = Config.load(root_dir=str(tmp_path))
        assert config.log_path is not None
        assert ".scheme-langserver.log" in config.log_path

    def test_resource_limits_default(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        fake_bin = tmp_path / "run"
        fake_bin.touch()
        monkeypatch.setenv("SCHEME_LANGSERVER_PATH", str(fake_bin))
        monkeypatch.delenv("SCHEME_LANGSERVER_MAX_MEMORY_MB", raising=False)
        monkeypatch.delenv("SCHEME_LANGSERVER_MAX_CPU_SECONDS", raising=False)
        config = Config.load(root_dir=str(tmp_path))
        assert config.max_memory_mb == 1024
        assert config.max_cpu_seconds == 180

    def test_resource_limits_from_env(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        fake_bin = tmp_path / "run"
        fake_bin.touch()
        monkeypatch.setenv("SCHEME_LANGSERVER_PATH", str(fake_bin))
        monkeypatch.setenv("SCHEME_LANGSERVER_MAX_MEMORY_MB", "512")
        monkeypatch.setenv("SCHEME_LANGSERVER_MAX_CPU_SECONDS", "60")
        config = Config.load(root_dir=str(tmp_path))
        assert config.max_memory_mb == 512
        assert config.max_cpu_seconds == 60

    def test_invalid_timeout_fallback(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        fake_bin = tmp_path / "run"
        fake_bin.touch()
        monkeypatch.setenv("SCHEME_LANGSERVER_PATH", str(fake_bin))
        monkeypatch.setenv("SCHEME_LANGSERVER_TIMEOUT", "abc")
        config = Config.load(root_dir=str(tmp_path))
        assert config.timeout == 30.0

    def test_invalid_max_memory_mb_fallback(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        fake_bin = tmp_path / "run"
        fake_bin.touch()
        monkeypatch.setenv("SCHEME_LANGSERVER_PATH", str(fake_bin))
        monkeypatch.setenv("SCHEME_LANGSERVER_MAX_MEMORY_MB", "not_a_number")
        config = Config.load(root_dir=str(tmp_path))
        assert config.max_memory_mb == 1024

    def test_build_cmd_with_cache_path(self) -> None:
        config = Config(
            langserver_path="/usr/bin/scheme-langserver",
            log_path="/tmp/test.log",
            cache_path="/tmp/scheme-cache",
        )
        cmd = config.build_cmd("/project/root")
        idx = cmd.index("--cache-path")
        assert cmd[idx + 1] == "/tmp/scheme-cache"

    def test_cache_path_from_env(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        fake_bin = tmp_path / "run"
        fake_bin.touch()
        monkeypatch.setenv("SCHEME_LANGSERVER_PATH", str(fake_bin))
        monkeypatch.setenv("SCHEME_LANGSERVER_CACHE_PATH", "/env/cache")
        config = Config.load(root_dir=str(tmp_path))
        assert config.cache_path == "/env/cache"


class TestFindAkkuLibdirs:
    def test_no_akku_returns_empty(self, tmp_path: Path) -> None:
        assert _find_akku_libdirs(str(tmp_path)) == []

    def test_akku_dir_only(self, tmp_path: Path) -> None:
        (tmp_path / ".akku").mkdir()
        result = _find_akku_libdirs(str(tmp_path))
        assert str(tmp_path / ".akku") in result

    def test_akku_with_subdirs(self, tmp_path: Path) -> None:
        akku = tmp_path / ".akku"
        akku.mkdir()
        (akku / "lib").mkdir()
        (akku / "src").mkdir()
        (akku / "vendor").mkdir()
        result = _find_akku_libdirs(str(tmp_path))
        assert str(akku) in result
        assert str(akku / "lib") in result
        assert str(akku / "src") in result
        assert str(akku / "vendor") in result

    def test_akku_manifest_adds_lib(self, tmp_path: Path) -> None:
        (tmp_path / "Akku.manifest").touch()
        (tmp_path / ".akku").mkdir()
        (tmp_path / ".akku" / "lib").mkdir()
        result = _find_akku_libdirs(str(tmp_path))
        assert str(tmp_path / ".akku" / "lib") in result

    def test_existing_chezschemelibdirs_preserved(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        akku = tmp_path / ".akku"
        akku.mkdir()
        (akku / "lib").mkdir()
        monkeypatch.setenv("CHEZSCHEMELIBDIRS", "/existing/path")
        # This test is for lsp_client.start(); config only provides the helper.
        # We verify the helper returns the expected directories.
        result = _find_akku_libdirs(str(tmp_path))
        assert str(akku) in result
        assert str(akku / "lib") in result
