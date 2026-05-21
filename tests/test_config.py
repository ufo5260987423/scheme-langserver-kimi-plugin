"""Tests for config module."""

from __future__ import annotations

from pathlib import Path

import pytest

from scheme_langserver_bridge.config import Config


class TestConfig:
    def test_build_cmd_basic(self) -> None:
        config = Config(
            langserver_path="/usr/bin/scheme-langserver",
            log_path="/tmp/test.log",
        )
        cmd = config.build_cmd("/project/root")
        assert cmd[0] == "/usr/bin/scheme-langserver"
        # scheme-langserver expects positional arguments:
        # <log-path> <multi-thread> <type-inference>
        assert cmd[1] == "/tmp/test.log"
        assert cmd[2] == "enable"
        assert cmd[3] == "enable"
        assert len(cmd) == 4

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
        assert cmd[1].endswith(".scheme-langserver.log")
        assert cmd[2] == "disable"
        assert cmd[3] == "disable"
        assert len(cmd) == 4

    def test_find_langserver_from_env(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        fake_bin = tmp_path / "scheme-langserver" / "run"
        fake_bin.parent.mkdir(parents=True, exist_ok=True)
        fake_bin.touch()
        monkeypatch.setenv("SCHEME_LANGSERVER_PATH", str(fake_bin))
        config = Config.from_env()
        assert config.langserver_path == str(fake_bin)

    def test_log_path_default(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        fake_bin = tmp_path / "run"
        fake_bin.touch()
        monkeypatch.setenv("SCHEME_LANGSERVER_PATH", str(fake_bin))
        monkeypatch.delenv("SCHEME_LANGSERVER_LOG_PATH", raising=False)
        config = Config.from_env()
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
        config = Config.from_env()
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
        config = Config.from_env()
        assert config.max_memory_mb == 512
        assert config.max_cpu_seconds == 60
