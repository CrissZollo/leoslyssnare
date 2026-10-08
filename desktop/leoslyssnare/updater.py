"""Finds out whether a newer release exists on GitHub and installs it.

Releases are tagged desktop-v<version> and hold the files for all three
platforms. The check is a single request to GitHub's API; it never sends
anything about the user or their recordings, and a failure is silent (the app
is meant to work offline)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import ssl
import subprocess
import sys
import urllib.request
from dataclasses import dataclass, field
from typing import Callable

from . import __version__
from .paths import system_env

REPO = "CrissZollo/leoslyssnare"
RELEASES_URL = f"https://api.github.com/repos/{REPO}/releases?per_page=10"
TAG_PREFIX = "desktop-v"


class UpdateError(Exception):
    pass


@dataclass
class Asset:
    name: str
    url: str
    size: int = 0
    sha256: str | None = None


@dataclass
class Release:
    version: str
    page: str  # the release's web page, with the notes
    assets: list[Asset] = field(default_factory=list)


def parse_version(text: str) -> tuple[int, ...]:
    """'0.1.10' -> (0, 1, 10). Anything after the numbers (-beta…) is ignored."""
    match = re.match(r"v?(\d+(?:\.\d+)*)", text.strip())
    if not match:
        raise ValueError(f"Not a version: {text!r}")
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer(candidate: str, current: str = __version__) -> bool:
    return parse_version(candidate) > parse_version(current)


def _context() -> ssl.SSLContext:
    import certifi

    # SSL_CERT_FILE lets it work behind a proxy with its own certificate.
    return ssl.create_default_context(cafile=os.environ.get("SSL_CERT_FILE") or certifi.where())


def _open(url: str, timeout: float):
    request = urllib.request.Request(url, headers={"User-Agent": "LeosLyssnare",
                                                   "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(request, context=_context(), timeout=timeout)


def latest_release(releases: list[dict]) -> Release | None:
    """The highest published desktop release in GitHub's list."""
    best: Release | None = None
    for item in releases:
        tag = item.get("tag_name") or ""
        if item.get("draft") or item.get("prerelease") or not tag.startswith(TAG_PREFIX):
            continue
        try:
            parse_version(tag[len(TAG_PREFIX):])
        except ValueError:
            continue
        version = tag[len(TAG_PREFIX):]
        if best is not None and parse_version(version) <= parse_version(best.version):
            continue
        assets = []
        for asset in item.get("assets", []):
            digest = asset.get("digest") or ""
            assets.append(Asset(asset["name"], asset["browser_download_url"], asset.get("size", 0),
                                digest[len("sha256:"):] if digest.startswith("sha256:") else None))
        best = Release(version, item.get("html_url") or f"https://github.com/{REPO}/releases", assets)
    return best


def check() -> Release | None:
    """The newest release if it's newer than this app, else None.
    Raises on network trouble; callers decide whether to show it."""
    with _open(RELEASES_URL, 15) as response:
        release = latest_release(json.load(response))
    return release if release and is_newer(release.version) else None


# MARK: - What can be installed in place

WINDOWS_INSTALLER = "windows-installer"
APPIMAGE = "appimage"


def install_kind() -> str | None:
    """How this copy of the app can replace itself, or None when it can only
    send the user to the download page (portable .zip, running from source)."""
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        # Inno Setup puts its uninstaller next to the program.
        if os.path.exists(os.path.join(os.path.dirname(sys.executable), "unins000.exe")):
            return WINDOWS_INSTALLER
    if sys.platform.startswith("linux") and os.environ.get("APPIMAGE"):
        return APPIMAGE
    return None


def pick_asset(release: Release, kind: str | None) -> Asset | None:
    suffix = {WINDOWS_INSTALLER: "-setup.exe", APPIMAGE: ".AppImage"}.get(kind or "")
    return next((a for a in release.assets if suffix and a.name.endswith(suffix)), None)


# MARK: - Download and install

Progress = Callable[[float], None]


def download(asset: Asset, destination: str, progress: Progress, cancelled: Callable[[], bool]) -> None:
    """Downloads to `destination`, checking size and checksum, and leaves
    nothing behind on failure."""
    sha = hashlib.sha256()
    received = 0
    try:
        with _open(asset.url, 60) as response, open(destination, "wb") as out:
            total = int(response.headers.get("Content-Length") or asset.size or 0)
            while chunk := response.read(1 << 16):
                if cancelled():
                    raise UpdateError("Cancelled")
                out.write(chunk)
                sha.update(chunk)
                received += len(chunk)
                if total:
                    progress(min(received / total, 1.0))
        if asset.size and received != asset.size:
            raise UpdateError("The download was incomplete. Try again.")
        if asset.sha256 and sha.hexdigest() != asset.sha256:
            raise UpdateError("The downloaded file doesn't match its checksum. Try again.")
    except BaseException:
        try:
            os.remove(destination)
        except OSError:
            pass
        raise


def install_windows(setup_path: str) -> None:
    """Starts the installer silently; it closes this app if needed, upgrades
    in place and starts the new version. The caller then quits."""
    flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([setup_path, "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"],
                     creationflags=flags, close_fds=True)


def replace_appimage(new_path: str) -> str:
    """Swaps the running AppImage for the downloaded file (a rename is fine on
    Linux even while the old one runs) and returns its path."""
    target = os.environ["APPIMAGE"]
    os.chmod(new_path, 0o755)
    os.replace(new_path, target)
    return target


def relaunch_appimage(path: str) -> None:
    """Starts the new AppImage a moment from now, once this process has quit."""
    env = system_env()
    for name in ("APPIMAGE", "APPDIR", "ARGV0", "OWD"):
        env.pop(name, None)
    subprocess.Popen(["sh", "-c", 'sleep 1; exec "$0"', path], env=env, start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
