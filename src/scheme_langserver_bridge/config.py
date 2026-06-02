"""Configuration for the scheme-langserver bridge."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import auto_update, project_config

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
    report_dir: str | None = None
    auto_update: bool = True

    # Populated by load(), not from env/file directly.
    version_info: dict[str, Any] = field(default_factory=dict, repr=False)
    server_source: str = "unknown"

    @classmethod
    def load(cls, root_dir: str | None = None) -> Config:
        """Load configuration from project config, environment variables, and defaults.

        If no scheme-langserver executable is found locally, attempts to
        auto-download the latest release (unless disabled).
        """
        # Start with defaults and env vars.
        langserver_path = os.environ.get("SCHEME_LANGSERVER_PATH", "")
        log_path = os.environ.get("SCHEME_LANGSERVER_LOG_PATH")
        if not log_path:
            log_path = str(Path.cwd() / ".scheme-langserver.log")

        multi_thread = os.environ.get("SCHEME_LANGSERVER_MULTI_THREAD", "enable")
        type_inference = os.environ.get("SCHEME_LANGSERVER_TYPE_INFERENCE", "enable")
        top_environment = os.environ.get("SCHEME_LANGSERVER_TOP_ENVIRONMENT", "R6RS")
        debug = os.environ.get("SCHEME_LANGSERVER_DEBUG", "disable")
        timeout = _env_float("SCHEME_LANGSERVER_TIMEOUT", 30.0)
        completion_timeout = _env_float("SCHEME_LANGSERVER_COMPLETION_TIMEOUT", 30.0)
        max_memory_mb = _env_int("SCHEME_LANGSERVER_MAX_MEMORY_MB", 1024)
        max_cpu_seconds = _env_int("SCHEME_LANGSERVER_MAX_CPU_SECONDS", 180)
        report_dir = os.environ.get("SCHEME_BRIDGE_REPORT_DIR")
        auto_update = _env_bool("SCHEME_LANGSERVER_AUTO_UPDATE", True)

        # Overlay project config if available.
        proj_cfg: dict[str, Any] = {}
        if root_dir is not None:
            proj_cfg = project_config.load_project_config(root_dir)
            if proj_cfg:
                logger.info("Loaded project config from %s", root_dir)

        # Project config overrides env vars.
        if "langserver_path" in proj_cfg:
            langserver_path = str(proj_cfg["langserver_path"])
        if "log_path" in proj_cfg:
            log_path = str(proj_cfg["log_path"])
        if "multi_thread" in proj_cfg:
            multi_thread = str(proj_cfg["multi_thread"])
        if "type_inference" in proj_cfg:
            type_inference = str(proj_cfg["type_inference"])
        if "top_environment" in proj_cfg:
            top_environment = str(proj_cfg["top_environment"])
        if "auto_update" in proj_cfg:
            auto_update = bool(proj_cfg["auto_update"])

        # Resolve langserver_path.
        source = "unknown"
        version_info: dict[str, Any] = {}

        if langserver_path and Path(langserver_path).exists():
            source = "project-config" if "langserver_path" in proj_cfg else "env-var"
        else:
            # Try discovery (PATH, known paths).
            discovered = cls._try_discover()
            if discovered is not None:
                langserver_path = discovered
                source = "path"
            elif auto_update:
                # Auto-download latest.
                try:
                    path, dl_source, vinfo = auto_update.ensure_latest_binary(
                        auto_update=True
                    )
                    langserver_path = path
                    source = dl_source
                    version_info = vinfo
                except Exception as exc:
                    logger.warning("Auto-download failed: %s", exc)
                    raise RuntimeError(
                        "scheme-langserver executable not found and auto-download failed. "
                        f"Error: {exc}. "
                        "Set SCHEME_LANGSERVER_PATH or install manually."
                    ) from exc
            else:
                raise RuntimeError(
                    "scheme-langserver executable not found. "
                    "Set SCHEME_LANGSERVER_PATH, ensure it is in PATH, "
                    "or enable auto_update."
                )

        return cls(
            langserver_path=langserver_path,
            log_path=log_path,
            multi_thread=multi_thread,
            type_inference=type_inference,
            top_environment=top_environment,
            debug=debug,
            timeout=timeout,
            completion_timeout=completion_timeout,
            max_memory_mb=max_memory_mb,
            max_cpu_seconds=max_cpu_seconds,
            report_dir=report_dir,
            auto_update=auto_update,
            version_info=version_info,
            server_source=source,
        )

    @classmethod
    def from_env(cls) -> Config:
        """Legacy entry point for tests that don't supply a root_dir."""
        return cls.load(root_dir=None)

    @staticmethod
    def _try_discover() -> str | None:
        """Try to discover scheme-langserver via PATH or known paths."""
        for name in ("scheme-langserver", "run"):
            if (path := _which(name)) is not None:
                return path

        known_paths = [
            Path.cwd() / "scheme-langserver" / "run",
            Path.cwd().parent / "scheme-langserver" / "run",
            Path.home() / "Documents" / "workspace" / "scheme-langserver" / "run",
        ]
        for p in known_paths:
            if p.exists():
                return str(p.resolve())
        return None

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


def _env_bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.lower() in ("true", "1", "yes", "on")


def _find_akku_libdirs(root_dir: str) -> list[str]:
    """Find Akku library directories under root_dir."""
    libdirs = []
    root = Path(root_dir)
    akku_dir = root / ".akku"
    if akku_dir.exists():
        libdirs.append(str(akku_dir))
        for sub in ("lib", "src", "vendor"):
            p = akku_dir / sub
            if p.exists():
                libdirs.append(str(p))
    if (root / "Akku.manifest").exists() and akku_dir.exists():
        libdirs.append(str(akku_dir / "lib"))
    return libdirs


def _which(name: str) -> str | None:
    """Simple which implementation."""
    for path in os.environ.get("PATH", "").split(os.pathsep):
        full = Path(path) / name
        if full.exists() and full.is_file():
            return str(full.resolve())
    return None
