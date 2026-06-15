"""Auto-download and cache scheme-langserver from GitHub Releases.

Uses GitHub's static redirect URL (not the API) to avoid rate limits:
  https://github.com/ufo5260987423/scheme-langserver/releases/latest/download/<asset>
"""

from __future__ import annotations

import json
import logging
import os
import platform
import re
import stat
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_GITHUB_LATEST_URL = (
    "https://github.com/ufo5260987423/scheme-langserver/releases/latest/download/"
)
_GITHUB_API_LATEST = (
    "https://api.github.com/repos/ufo5260987423/scheme-langserver/releases/latest"
)

# TTL for version-check cache (seconds)
_VERSION_CHECK_TTL = 3600


def get_cache_dir() -> Path:
    """Return the base cache directory for auto-downloaded binaries."""
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        base = Path(xdg)
    else:
        base = Path.home() / ".cache"
    return base / "scheme-langserver-bridge"


def _asset_name_for_platform() -> str | None:
    """Map current platform to the GitHub release asset name."""
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "linux" and machine in ("x86_64", "amd64"):
        return "scheme-langserver-x86_64-linux-glibc"
    return None


def _version_check_cache_path() -> Path:
    return get_cache_dir() / "version-check.json"


def _read_cached_version_info() -> dict[str, Any] | None:
    """Read cached version-check result if still fresh."""
    path = _version_check_cache_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        import time

        if time.time() - data.get("checked_at", 0) < _VERSION_CHECK_TTL:
            return data
    except Exception:
        pass
    return None


def _write_cached_version_info(info: dict[str, Any]) -> None:
    """Write version-check result to cache."""
    import time

    path = _version_check_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    info["checked_at"] = time.time()
    path.write_text(json.dumps(info, indent=2), encoding="utf-8")


def _normalize_tag(tag: str) -> str:
    """Normalize a GitHub release tag to a version string.

    scheme-langserver switched from tags like ``2.1.2`` to ``v2.1.3``; we strip
    the leading ``v``/``V`` so cache directories and version comparisons remain
    consistent.
    """
    return tag.lstrip("vV")


def _parse_version_from_location(location: str) -> str | None:
    """Extract tag version from a GitHub redirect Location URL.

    Examples:
      /releases/download/2.1.0/scheme-langserver-x86_64-linux-glibc -> 2.1.0
      /releases/download/v2.1.3/scheme-langserver-x86_64-linux-glibc -> v2.1.3
    """
    match = re.search(r"/releases/download/([^/]+)/", location)
    if match:
        return match.group(1)
    return None


def check_latest_version() -> dict[str, Any]:
    """Check the latest release version without using GitHub API.

    Returns a dict with at least:
      - tag: str | None
      - asset_url: str | None
      - sha256: str | None
      - error: str | None
    """
    cached = _read_cached_version_info()
    if cached is not None:
        return cached

    asset_name = _asset_name_for_platform()
    if asset_name is None:
        result = {
            "tag": None,
            "asset_url": None,
            "sha256": None,
            "error": f"Unsupported platform: {platform.system()} {platform.machine()}",
        }
        _write_cached_version_info(result)
        return result

    url = _GITHUB_LATEST_URL + asset_name

    # Use HEAD and intercept the first 302 to read the version from Location.
    class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
        def http_error_302(self, req, fp, code, msg, headers):  # type: ignore[override]
            return fp

        http_error_301 = http_error_303 = http_error_307 = http_error_302

    opener = urllib.request.build_opener(NoRedirectHandler)
    req = urllib.request.Request(url, method="HEAD")

    try:
        resp = opener.open(req, timeout=15)
        location = resp.headers.get("Location", "")
        tag = _parse_version_from_location(location)
        if tag is None:
            result = {
                "tag": None,
                "asset_url": None,
                "sha256": None,
                "error": f"Could not parse version from redirect: {location}",
            }
            _write_cached_version_info(result)
            return result

        # Build the concrete download URL (follow the first redirect manually).
        download_url = f"https://github.com/ufo5260987423/scheme-langserver/releases/download/{tag}/{asset_name}"

        # Try to fetch SHA256 from GitHub API (one call, cached for TTL).
        sha256 = _fetch_sha256_from_api(tag, asset_name)

        result = {
            "tag": tag,
            "asset_url": download_url,
            "sha256": sha256,
            "error": None,
        }
        _write_cached_version_info(result)
        return result

    except urllib.error.HTTPError as exc:
        result = {
            "tag": None,
            "asset_url": None,
            "sha256": None,
            "error": f"HTTP {exc.code}: {exc.reason}",
        }
        _write_cached_version_info(result)
        return result
    except Exception as exc:
        result = {
            "tag": None,
            "asset_url": None,
            "sha256": None,
            "error": str(exc),
        }
        _write_cached_version_info(result)
        return result


def _fetch_sha256_from_api(tag: str, asset_name: str) -> str | None:
    """Fetch SHA256 digest from GitHub API for a specific release.

    This is cached independently via _VERSION_CHECK_TTL, so it is not
    called frequently enough to hit the API rate limit.
    """
    try:
        req = urllib.request.Request(
            _GITHUB_API_LATEST,
            headers={"Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        for asset in data.get("assets", []):
            if asset.get("name") == asset_name:
                digest = asset.get("digest", "")
                if digest.startswith("sha256:"):
                    return digest[len("sha256:"):]
        return None
    except Exception:
        return None


def _compute_sha256(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def get_cached_binary_path(tag: str) -> Path | None:
    """Return the path to a cached binary if it exists."""
    asset_name = _asset_name_for_platform()
    if asset_name is None:
        return None
    path = get_cache_dir() / "versions" / _normalize_tag(tag) / asset_name
    if path.exists():
        return path
    return None


def download_binary(tag: str, url: str, expected_sha256: str | None = None) -> Path:
    """Download a release asset to the cache directory.

    Returns the path to the cached binary.
    """
    asset_name = _asset_name_for_platform()
    if asset_name is None:
        raise RuntimeError(
            f"Unsupported platform: {platform.system()} {platform.machine()}"
        )

    version = _normalize_tag(tag)
    cache_dir = get_cache_dir() / "versions" / version
    cache_dir.mkdir(parents=True, exist_ok=True)
    dest = cache_dir / asset_name

    logger.info("Downloading scheme-langserver %s from %s", tag, url)
    urllib.request.urlretrieve(url, dest)

    # Verify SHA256 if available.
    if expected_sha256:
        actual = _compute_sha256(dest)
        if actual.lower() != expected_sha256.lower():
            dest.unlink(missing_ok=True)
            raise RuntimeError(
                f"SHA256 mismatch for {tag}: expected {expected_sha256}, got {actual}"
            )

    # Make executable.
    dest.chmod(dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    logger.info("scheme-langserver %s cached at %s", tag, dest)
    return dest


def ensure_latest_binary(auto_update: bool = True) -> tuple[str, str, dict[str, Any]]:
    """Ensure a scheme-langserver binary is available.

    Returns (path, source, version_info) where:
      - path: absolute path to the binary
      - source: one of "cache", "auto-downloaded", "project-config", "env-var", "path"
      - version_info: dict with keys tag, latest, up_to_date, note
    """
    latest = check_latest_version()
    if latest.get("error"):
        raise RuntimeError(
            f"Cannot determine latest scheme-langserver version: {latest['error']}"
        )

    tag = latest["tag"]
    assert tag is not None
    version = _normalize_tag(tag)

    # Check if we already have this version cached.
    cached = get_cached_binary_path(version)
    if cached is not None:
        return (
            str(cached),
            "cache",
            {"tag": version, "latest": version, "up_to_date": True, "note": ""},
        )

    # Check for any older cached version.
    all_tags = _list_cached_tags()
    if all_tags:
        current_tag = max(all_tags, key=_semver_key)
        cached_old = get_cached_binary_path(current_tag)
        if cached_old is not None:
            if auto_update and _semver_key(version) > _semver_key(current_tag):
                # Auto-update to newer version.
                path = download_binary(
                    tag, latest["asset_url"], latest.get("sha256")
                )
                return (
                    str(path),
                    "auto-downloaded",
                    {
                        "tag": version,
                        "latest": version,
                        "up_to_date": True,
                        "note": f"Auto-updated from {current_tag} to {version}",
                    },
                )
            else:
                # Use existing older version but report that newer is available.
                return (
                    str(cached_old),
                    "cache",
                    {
                        "tag": current_tag,
                        "latest": version,
                        "up_to_date": False,
                        "note": (
                            f"Newer version {version} available; "
                            f"set auto_update=true to auto-update"
                        ),
                    },
                )

    # No cache at all — download.
    path = download_binary(tag, latest["asset_url"], latest.get("sha256"))
    return (
        str(path),
        "auto-downloaded",
        {"tag": version, "latest": version, "up_to_date": True, "note": ""},
    )


def _list_cached_tags() -> list[str]:
    """List all normalized version tags present in the cache."""
    versions_dir = get_cache_dir() / "versions"
    if not versions_dir.exists():
        return []
    tags = {_normalize_tag(d.name) for d in versions_dir.iterdir() if d.is_dir()}
    return sorted(tags)


def _semver_key(tag: str) -> tuple[int, ...]:
    """A simple sort key for version tags like '2.1.0'."""
    parts = tag.lstrip("v").split(".")
    result: list[int] = []
    for p in parts:
        try:
            result.append(int(p))
        except ValueError:
            result.append(0)
    return tuple(result)
