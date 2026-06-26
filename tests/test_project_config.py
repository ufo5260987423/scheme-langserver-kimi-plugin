"""Tests for project_config module."""

from __future__ import annotations

from pathlib import Path

import pytest

from scheme_langserver_bridge import project_config


class TestLoadProjectConfig:
    def test_no_config_returns_empty(self, tmp_path: Path) -> None:
        assert project_config.load_project_config(str(tmp_path)) == {}

    def test_toml_config(self, tmp_path: Path) -> None:
        (tmp_path / ".scheme-langserver.toml").write_text(
            'langserver_path = "/custom/run"\n'
            'multi_thread = "disable"\n'
            'auto_update = false\n',
            encoding="utf-8",
        )
        cfg = project_config.load_project_config(str(tmp_path))
        assert cfg["langserver_path"] == "/custom/run"
        assert cfg["multi_thread"] == "disable"
        assert cfg["auto_update"] is False

    def test_json_config(self, tmp_path: Path) -> None:
        (tmp_path / ".scheme-langserver.json").write_text(
            '{"type_inference": "disable", "auto_update": true}',
            encoding="utf-8",
        )
        cfg = project_config.load_project_config(str(tmp_path))
        assert cfg["type_inference"] == "disable"
        assert cfg["auto_update"] is True

    def test_toml_takes_precedence_over_json(self, tmp_path: Path) -> None:
        (tmp_path / ".scheme-langserver.toml").write_text(
            'top_environment = "R6RS"\n', encoding="utf-8"
        )
        (tmp_path / ".scheme-langserver.json").write_text(
            '{"top_environment": "R7RS"}', encoding="utf-8"
        )
        cfg = project_config.load_project_config(str(tmp_path))
        # TOML is checked first per _CONFIG_FILENAMES ordering.
        assert cfg["top_environment"] == "R6RS"

    def test_malformed_toml_skips(self, tmp_path: Path) -> None:
        (tmp_path / ".scheme-langserver.toml").write_text(
            "not valid toml [[", encoding="utf-8"
        )
        assert project_config.load_project_config(str(tmp_path)) == {}

    def test_malformed_json_skips(self, tmp_path: Path) -> None:
        (tmp_path / ".scheme-langserver.json").write_text(
            "not json", encoding="utf-8"
        )
        assert project_config.load_project_config(str(tmp_path)) == {}

    def test_non_dict_json_skips(self, tmp_path: Path) -> None:
        (tmp_path / ".scheme-langserver.json").write_text(
            "[1, 2, 3]", encoding="utf-8"
        )
        assert project_config.load_project_config(str(tmp_path)) == {}

    def test_bool_coercion_from_string(self) -> None:
        assert project_config._to_bool("true") is True
        assert project_config._to_bool("1") is True
        assert project_config._to_bool("yes") is True
        assert project_config._to_bool("on") is True
        assert project_config._to_bool("false") is False
        assert project_config._to_bool("0") is False

    def test_bool_coercion_from_bool(self) -> None:
        assert project_config._to_bool(True) is True
        assert project_config._to_bool(False) is False

    def test_int_keys_parsed_from_toml(self, tmp_path: Path) -> None:
        (tmp_path / ".scheme-langserver.toml").write_text(
            "max_memory_mb = 4096\n"
            "max_cpu_seconds = 300\n",
            encoding="utf-8",
        )
        cfg = project_config.load_project_config(str(tmp_path))
        assert cfg["max_memory_mb"] == 4096
        assert cfg["max_cpu_seconds"] == 300

    def test_int_keys_coerced_from_string(self, tmp_path: Path) -> None:
        (tmp_path / ".scheme-langserver.toml").write_text(
            'max_memory_mb = "2048"\n',
            encoding="utf-8",
        )
        cfg = project_config.load_project_config(str(tmp_path))
        assert cfg["max_memory_mb"] == 2048

    def test_int_coercion_invalid_value_defaults_to_zero(self) -> None:
        assert project_config._to_int("not-a-number") == 0
        assert project_config._to_int(None) == 0

    def test_int_coercion_from_int(self) -> None:
        assert project_config._to_int(4096) == 4096
