"""Project-level configuration loader.

Supports `.scheme-langserver.toml` (preferred) and `.scheme-langserver.json`
in the project root directory. Project config overrides environment variables.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

try:
    import tomllib
except ImportError:
    tomllib = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

_CONFIG_FILENAMES = (".scheme-langserver.toml", ".scheme-langserver.json")

# Mapping from config keys to their expected types
_BOOL_KEYS = {"auto_update"}


def load_project_config(root_dir: str) -> dict[str, Any]:
    """Load project-level config if present.

    Returns an empty dict if no config file exists.
    """
    root = Path(root_dir)
    for name in _CONFIG_FILENAMES:
        path = root / name
        if path.exists():
            return _parse_config_file(path)
    return {}


def _parse_config_file(path: Path) -> dict[str, Any]:
    """Parse a TOML or JSON config file."""
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".toml":
        if tomllib is None:
            logger.warning(
                "Project config %s is TOML but tomllib is not available "
                "(requires Python 3.11+). Skipping.",
                path,
            )
            return {}
        try:
            data = tomllib.loads(text)
        except Exception as exc:
            logger.warning("Failed to parse TOML config %s: %s", path, exc)
            return {}
    else:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            logger.warning("Failed to parse JSON config %s: %s", path, exc)
            return {}

    if not isinstance(data, dict):
        logger.warning("Project config %s is not a dict. Skipping.", path)
        return {}

    return _normalize_values(data)


def _normalize_values(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize config values (e.g. coerce booleans from TOML)."""
    result: dict[str, Any] = {}
    for key, value in data.items():
        if key in _BOOL_KEYS:
            result[key] = _to_bool(value)
        else:
            result[key] = value
    return result


def _to_bool(value: Any) -> bool:
    """Coerce a value to bool."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("true", "1", "yes", "on")
    return bool(value)
