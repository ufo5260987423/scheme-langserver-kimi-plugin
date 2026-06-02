"""Crash report generation for scheme-langserver upstream debugging."""

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .config import Config
    from .document_sync import DocumentManager
    from .lsp_client import LspClient

logger = logging.getLogger(__name__)

_README_TEMPLATE = """# scheme-langserver 调试报告

> ⚠️ **隐私警告**：本报告包含你的源代码和项目文件结构。
> 在提交到公开的 GitHub Issue 之前，请仔细检查报告内容，
> 确认没有敏感信息（密码、密钥、私人数据）后再分享。

## 这是什么？

这份报告是在 scheme-langserver 语言服务器崩溃或行为异常时自动/手动生成的，
包含了复现问题所需的全部信息：

- **通信日志** (`ready-for-analyse.log`)：本次会话中客户端发给服务器的所有 LSP 消息
- **项目快照** (`project-snapshot/`)：当时打开的文件内容
- **服务器日志** (`stderr.log`, `server-log.log`)：错误输出和调试日志
- **环境信息** (`manifest.json`)：服务器版本、启动参数、操作系统等

## 如何提交给上游开发者？

1. 将整个目录打包为 `.zip` 或 `.tar.gz`
2. 前往 https://github.com/ufo5260987423/scheme-langserver/issues
3. 新建 Issue，描述你遇到的问题
4. 上传打包好的报告作为附件
5. 等待开发者回复

## 报告内容清单

| 文件 | 说明 |
|------|------|
| `ready-for-analyse.log` | 上游回放脚本可直接使用的 LSP 通信日志 |
| `lsp-incoming.log` | 服务器返回的响应和通知 |
| `stderr.log` | 服务器标准错误输出 |
| `server-log.log` | scheme-langserver 自己生成的日志 |
| `diagnostics.json` | 崩溃前已发布的诊断信息 |
| `manifest.json` | 环境元数据 |
| `project-snapshot/` | 打开的文件副本 |

---

生成时间：{generated_at}
生成原因：{reason}
"""


class CrashReporter:
    """Collect LSP traffic, project context, and environment info for upstream debugging."""

    def __init__(
        self,
        config: Config,
        max_outgoing_lines: int = 10_000,
        max_incoming_lines: int = 10_000,
        max_stderr_lines: int = 5_000,
    ) -> None:
        self._config = config
        self._client: LspClient | None = None
        self._doc_manager: DocumentManager | None = None

        self._outgoing_buffer: list[str] = []
        self._incoming_buffer: list[str] = []
        self._stderr_buffer: list[str] = []

        self._max_outgoing_lines = max_outgoing_lines
        self._max_incoming_lines = max_incoming_lines
        self._max_stderr_lines = max_stderr_lines

    # ------------------------------------------------------------------
    # Attachment
    # ------------------------------------------------------------------

    def attach(
        self, client: LspClient, doc_manager: DocumentManager | None = None
    ) -> None:
        """Wire the reporter to a running LspClient and optional DocumentManager."""
        self._client = client
        self._doc_manager = doc_manager

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record_outgoing(self, payload: str) -> None:
        """Record a client→server message in upstream replay format."""
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
        entry = f"read-message\n{timestamp}\n{payload}\n"
        self._outgoing_buffer.append(entry)
        if len(self._outgoing_buffer) > self._max_outgoing_lines:
            self._outgoing_buffer.pop(0)

    def record_incoming(self, payload: str) -> None:
        """Record a server→client message."""
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
        entry = f"{timestamp} {payload}\n"
        self._incoming_buffer.append(entry)
        if len(self._incoming_buffer) > self._max_incoming_lines:
            self._incoming_buffer.pop(0)

    def record_stderr(self, line: str) -> None:
        """Record a line from the server's stderr."""
        self._stderr_buffer.append(line)
        if len(self._stderr_buffer) > self._max_stderr_lines:
            self._stderr_buffer.pop(0)

    # ------------------------------------------------------------------
    # Report generation
    # ------------------------------------------------------------------

    def generate_report(
        self,
        output_dir: Path | None = None,
        reason: str = "manual",
    ) -> Path:
        """Assemble a debug report directory and return its path."""
        if output_dir is None:
            output_dir = self._default_report_dir()

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        self._write_readme(output_dir, reason)
        self._write_manifest(output_dir, reason)
        self._write_traffic_log(output_dir)
        self._write_incoming_log(output_dir)
        self._write_stderr_log(output_dir)
        self._copy_server_log(output_dir)
        self._write_diagnostics(output_dir)
        self._write_project_snapshot(output_dir)

        logger.info("Debug report generated: %s", output_dir)
        return output_dir

    def auto_generate_on_crash(self, reason: str = "crash") -> Path | None:
        """Convenience wrapper called automatically when a crash is detected."""
        try:
            return self.generate_report(reason=reason)
        except Exception as exc:
            logger.exception("Failed to generate crash report: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Internal writers
    # ------------------------------------------------------------------

    def _default_report_dir(self) -> Path:
        if self._config.report_dir:
            base = Path(self._config.report_dir)
        else:
            base = Path.cwd()
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        return base / f"scheme-langserver-debug-report-{ts}"

    def _write_readme(self, output_dir: Path, reason: str) -> None:
        generated_at = datetime.now(timezone.utc).isoformat()
        text = _README_TEMPLATE.format(generated_at=generated_at, reason=reason)
        (output_dir / "README.md").write_text(text, encoding="utf-8")

    def _write_manifest(self, output_dir: Path, reason: str) -> None:
        manifest: dict[str, Any] = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
            "bridge_version": "0.1.0",
            "platform": platform.platform(),
            "python_version": platform.python_version(),
            "server_path": self._config.langserver_path,
            "server_version": self._try_server_version(),
            "launch_args": self._config.build_cmd(""),
            "env_chezschemelibdirs": os.environ.get("CHEZSCHEMELIBDIRS", ""),
            "top_environment": self._config.top_environment,
            "multi_thread": self._config.multi_thread,
            "type_inference": self._config.type_inference,
        }

        if self._client is not None:
            rc = None
            if self._client.process is not None:
                try:
                    rc = int(self._client.process.returncode)
                except (TypeError, ValueError):
                    rc = None
            manifest["process_returncode"] = rc
            manifest["client_initialized"] = bool(self._client._initialized)
            manifest["client_crashed"] = bool(self._client._crashed)
            manifest["client_shutdown"] = bool(self._client._shutdown)

        (output_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _write_traffic_log(self, output_dir: Path) -> None:
        if self._outgoing_buffer:
            (output_dir / "ready-for-analyse.log").write_text(
                "".join(self._outgoing_buffer), encoding="utf-8"
            )

    def _write_incoming_log(self, output_dir: Path) -> None:
        if self._incoming_buffer:
            (output_dir / "lsp-incoming.log").write_text(
                "".join(self._incoming_buffer), encoding="utf-8"
            )

    def _write_stderr_log(self, output_dir: Path) -> None:
        if self._stderr_buffer:
            (output_dir / "stderr.log").write_text(
                "".join(self._stderr_buffer), encoding="utf-8"
            )

    def _copy_server_log(self, output_dir: Path) -> None:
        if self._config.log_path and Path(self._config.log_path).exists():
            try:
                shutil.copy2(self._config.log_path, output_dir / "server-log.log")
            except Exception as exc:
                logger.warning("Could not copy server log: %s", exc)

    def _write_diagnostics(self, output_dir: Path) -> None:
        diags: dict[str, Any] = {}
        if self._client is not None:
            raw = self._client.get_diagnostics()
            if isinstance(raw, dict):
                diags = raw
        (output_dir / "diagnostics.json").write_text(
            json.dumps(diags, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _write_project_snapshot(self, output_dir: Path) -> None:
        snap_dir = output_dir / "project-snapshot"
        snap_dir.mkdir(exist_ok=True)

        file_list: list[str] = []
        files_dir = snap_dir / "open-files"
        files_dir.mkdir(exist_ok=True)

        if self._doc_manager is not None:
            for uri in self._doc_manager.list_uris():
                doc = self._doc_manager.get(uri)
                if doc is None:
                    continue
                file_list.append(uri)
                safe_name = self._sanitize_filename(uri)
                (files_dir / safe_name).write_text(
                    doc.get("text", ""), encoding="utf-8"
                )

        (snap_dir / "file-list.txt").write_text(
            "\n".join(file_list) + "\n", encoding="utf-8"
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _try_server_version(self) -> str | None:
        """Attempt to get the scheme-langserver executable version."""
        try:
            result = subprocess.run(
                [self._config.langserver_path, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except Exception:
            pass

        try:
            result = subprocess.run(
                [self._config.langserver_path, "--help"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                first_line = result.stdout.strip().splitlines()[0]
                return first_line.strip()
        except Exception:
            pass

        return None

    @staticmethod
    def _sanitize_filename(uri: str) -> str:
        """Turn a file URI into a safe filesystem name."""
        name = uri.replace("file://", "").replace("/", "_")
        # Remove leading underscores from absolute paths
        name = name.lstrip("_")
        if not name:
            name = "unknown"
        # Limit length
        if len(name) > 200:
            name = name[:200]
        return name
