"""Tests for crash_reporter module."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from scheme_langserver_bridge.crash_reporter import CrashReporter
from scheme_langserver_bridge.document_sync import DocumentManager


class TestCrashReporterBasics:
    def test_init_empty_buffers(self) -> None:
        config = MagicMock()
        config.report_dir = None
        reporter = CrashReporter(config)
        assert reporter._outgoing_buffer == []
        assert reporter._incoming_buffer == []
        assert reporter._stderr_buffer == []

    def test_record_outgoing_format(self) -> None:
        config = MagicMock()
        config.report_dir = None
        reporter = CrashReporter(config)
        reporter.record_outgoing('{"method":"initialize"}')

        assert len(reporter._outgoing_buffer) == 1
        entry = reporter._outgoing_buffer[0]
        assert entry.startswith("read-message\n")
        assert "{\"method\":\"initialize\"}" in entry

    def test_record_incoming_format(self) -> None:
        config = MagicMock()
        config.report_dir = None
        reporter = CrashReporter(config)
        reporter.record_incoming('{"id":1,"result":{}}')

        assert len(reporter._incoming_buffer) == 1
        entry = reporter._incoming_buffer[0]
        assert "{\"id\":1,\"result\":{}}" in entry

    def test_record_stderr(self) -> None:
        config = MagicMock()
        config.report_dir = None
        reporter = CrashReporter(config)
        reporter.record_stderr("Error: something went wrong")

        assert len(reporter._stderr_buffer) == 1
        assert reporter._stderr_buffer[0] == "Error: something went wrong"

    def test_outgoing_ring_buffer(self) -> None:
        config = MagicMock()
        config.report_dir = None
        reporter = CrashReporter(config, max_outgoing_lines=3)
        reporter.record_outgoing('"payload-1"')
        reporter.record_outgoing('"payload-2"')
        reporter.record_outgoing('"payload-3"')
        reporter.record_outgoing('"payload-4"')

        assert len(reporter._outgoing_buffer) == 3
        assert "payload-1" not in reporter._outgoing_buffer[0]
        assert "payload-4" in reporter._outgoing_buffer[-1]


class TestReportGeneration:
    def test_generates_all_expected_files(self, tmp_path: Path) -> None:
        config = MagicMock()
        config.report_dir = None
        config.langserver_path = "/fake/scheme-langserver"
        config.log_path = str(tmp_path / "server.log")
        config.top_environment = "R6RS"
        config.multi_thread = "enable"
        config.type_inference = "enable"
        config.build_cmd = MagicMock(return_value=["/fake/run", "/log", "enable", "enable"])

        reporter = CrashReporter(config)
        reporter.record_outgoing('{"method":"initialize"}')
        reporter.record_incoming('{"id":1,"result":{}}')
        reporter.record_stderr("some stderr\n")

        # Create a fake server log
        (tmp_path / "server.log").write_text("server output\n")

        report_dir = reporter.generate_report(output_dir=tmp_path / "report")

        assert report_dir.exists()
        assert (report_dir / "README.md").exists()
        assert (report_dir / "manifest.json").exists()
        assert (report_dir / "ready-for-analyse.log").exists()
        assert (report_dir / "lsp-incoming.log").exists()
        assert (report_dir / "stderr.log").exists()
        assert (report_dir / "server-log.log").exists()
        assert (report_dir / "diagnostics.json").exists()
        assert (report_dir / "project-snapshot").exists()

    def test_ready_for_analyse_log_content(self, tmp_path: Path) -> None:
        config = MagicMock()
        config.report_dir = None
        config.langserver_path = "/fake/scheme-langserver"
        config.log_path = None
        config.top_environment = "R6RS"
        config.multi_thread = "enable"
        config.type_inference = "enable"
        config.build_cmd = MagicMock(return_value=["/fake/run"])

        reporter = CrashReporter(config)
        reporter.record_outgoing('{"method":"initialize"}')
        reporter.record_outgoing('{"method":"textDocument/didOpen"}')

        report_dir = reporter.generate_report(output_dir=tmp_path / "report")
        log = (report_dir / "ready-for-analyse.log").read_text()

        assert log.count("read-message") == 2
        assert "{\"method\":\"initialize\"}" in log
        assert "{\"method\":\"textDocument/didOpen\"}" in log

    def test_manifest_json_content(self, tmp_path: Path) -> None:
        config = MagicMock()
        config.report_dir = None
        config.langserver_path = "/fake/scheme-langserver"
        config.log_path = None
        config.top_environment = "R7RS"
        config.multi_thread = "disable"
        config.type_inference = "disable"
        config.build_cmd = MagicMock(return_value=["/fake/run", "/log", "disable", "disable"])

        reporter = CrashReporter(config)
        report_dir = reporter.generate_report(output_dir=tmp_path / "report", reason="test")
        manifest = json.loads((report_dir / "manifest.json").read_text())

        assert manifest["reason"] == "test"
        assert manifest["server_path"] == "/fake/scheme-langserver"
        assert manifest["top_environment"] == "R7RS"
        assert manifest["multi_thread"] == "disable"
        assert manifest["launch_args"] == ["/fake/run", "/log", "disable", "disable"]
        assert "generated_at" in manifest

    def test_readme_contains_privacy_warning(self, tmp_path: Path) -> None:
        config = MagicMock()
        config.report_dir = None
        config.langserver_path = "/fake/scheme-langserver"
        config.log_path = None
        config.top_environment = "R6RS"
        config.multi_thread = "enable"
        config.type_inference = "enable"
        config.build_cmd = MagicMock(return_value=["/fake/run"])

        reporter = CrashReporter(config)
        report_dir = reporter.generate_report(output_dir=tmp_path / "report")
        readme = (report_dir / "README.md").read_text()

        assert "隐私警告" in readme or "隐私" in readme or "源代码" in readme
        assert "ready-for-analyse.log" in readme

    def test_project_snapshot_with_open_files(self, tmp_path: Path) -> None:
        config = MagicMock()
        config.report_dir = None
        config.langserver_path = "/fake/scheme-langserver"
        config.log_path = None
        config.top_environment = "R6RS"
        config.multi_thread = "enable"
        config.type_inference = "enable"
        config.build_cmd = MagicMock(return_value=["/fake/run"])

        reporter = CrashReporter(config)

        mock_doc_mgr = MagicMock()
        mock_doc_mgr.list_uris.return_value = ["file:///project/test.scm"]
        mock_doc_mgr.get.return_value = {
            "uri": "file:///project/test.scm",
            "version": 3,
            "text": "(define x 1)",
            "language_id": "scheme",
        }
        reporter.attach(MagicMock(), mock_doc_mgr)

        report_dir = reporter.generate_report(output_dir=tmp_path / "report")
        snap = report_dir / "project-snapshot"
        assert (snap / "file-list.txt").exists()
        files_dir = snap / "open-files"
        assert any(files_dir.iterdir())

        file_list = (snap / "file-list.txt").read_text()
        assert "file:///project/test.scm" in file_list

    def test_auto_generate_on_crash_returns_path(self, tmp_path: Path) -> None:
        config = MagicMock()
        config.report_dir = str(tmp_path)
        config.langserver_path = "/fake/scheme-langserver"
        config.log_path = None
        config.top_environment = "R6RS"
        config.multi_thread = "enable"
        config.type_inference = "enable"
        config.build_cmd = MagicMock(return_value=["/fake/run"])

        reporter = CrashReporter(config)
        reporter.record_outgoing('{"method":"initialize"}')

        path = reporter.auto_generate_on_crash(reason="test-crash")
        assert path is not None
        assert path.exists()
        assert (path / "manifest.json").exists()

    def test_default_report_dir_uses_cwd(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config = MagicMock()
        config.report_dir = None
        config.langserver_path = "/fake"
        config.log_path = None
        config.top_environment = "R6RS"
        config.multi_thread = "enable"
        config.type_inference = "enable"
        config.build_cmd = MagicMock(return_value=["/fake/run"])

        monkeypatch.chdir(tmp_path)
        reporter = CrashReporter(config)
        report_dir = reporter.generate_report()

        assert report_dir.parent == tmp_path
        assert report_dir.name.startswith("scheme-langserver-debug-report-")
