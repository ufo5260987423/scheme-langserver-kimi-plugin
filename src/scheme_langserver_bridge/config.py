"""Configuration for the scheme-langserver bridge."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class Config:
    """Bridge configuration."""

    langserver_path: str
    log_path: str | None
    multi_thread: str = "enable"
    type_inference: str = "enable"
    top_environment: str = "R6RS"
    debug: str = "disable"
    timeout: float = 30.0
    completion_timeout: float = 30.0
    max_memory_mb: int = 1024
    max_cpu_seconds: int = 180

    @classmethod
    def from_env(cls) -> Config:
        """Load configuration from environment variables."""
        langserver_path = cls._find_langserver()
        log_path = os.environ.get("SCHEME_LANGSERVER_LOG_PATH")
        if not log_path:
            log_path = str(Path.cwd() / ".scheme-langserver.log")
        return cls(
            langserver_path=langserver_path,
            log_path=log_path,
            multi_thread=os.environ.get("SCHEME_LANGSERVER_MULTI_THREAD", "enable"),
            type_inference=os.environ.get("SCHEME_LANGSERVER_TYPE_INFERENCE", "enable"),
            top_environment=os.environ.get("SCHEME_LANGSERVER_TOP_ENVIRONMENT", "R6RS"),
            debug=os.environ.get("SCHEME_LANGSERVER_DEBUG", "disable"),
            timeout=_env_float("SCHEME_LANGSERVER_TIMEOUT", 30.0),
            completion_timeout=_env_float(
                "SCHEME_LANGSERVER_COMPLETION_TIMEOUT", 30.0
            ),
            max_memory_mb=_env_int("SCHEME_LANGSERVER_MAX_MEMORY_MB", 1024),
            max_cpu_seconds=_env_int("SCHEME_LANGSERVER_MAX_CPU_SECONDS", 180),
        )

    @staticmethod
    def _find_langserver() -> str:
        """Discover scheme-langserver executable."""
        # 1. Environment variable
        if (path := os.environ.get("SCHEME_LANGSERVER_PATH")) and Path(path).exists():
            return str(Path(path).resolve())

        # 2. PATH
        for name in ("scheme-langserver", "run"):
            if (path := _which(name)) is not None:
                return path

        # 3. Known local development paths (fallback for dev environments)
        known_paths = [
            Path.cwd() / "scheme-langserver" / "run",
            Path.cwd().parent / "scheme-langserver" / "run",
            Path.home() / "Documents" / "workspace" / "scheme-langserver" / "run",
        ]
        for p in known_paths:
            if p.exists():
                return str(p.resolve())

        raise RuntimeError(
            "scheme-langserver executable not found. "
            "Set SCHEME_LANGSERVER_PATH or ensure it is in PATH."
        )

    def build_cmd(self, root_dir: str) -> list[str]:
        """Build the command to launch scheme-langserver.

        scheme-langserver expects positional arguments:
            <log-path> <multi-thread> <type-inference>
        Not flag-style arguments.
        """
        log_path = self.log_path or str(Path(root_dir) / ".scheme-langserver.log")
        cmd = [
            self.langserver_path,
            log_path,
            self.multi_thread,
            self.type_inference,
        ]
        return cmd


def _env_float(name: str, default: float) -> float:
    val = os.environ.get(name)
    if val is None:
        return default
    try:
        return float(val)
    except ValueError:
        logger.warning("Invalid %s=%r, using default %s", name, val, default)
        return default


def _env_int(name: str, default: int) -> int:
    val = os.environ.get(name)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        logger.warning("Invalid %s=%r, using default %s", name, val, default)
        return default


def _which(name: str) -> str | None:
    """Simple which implementation."""
    for path in os.environ.get("PATH", "").split(os.pathsep):
        full = Path(path) / name
        if full.exists() and full.is_file():
            return str(full.resolve())
    return None
