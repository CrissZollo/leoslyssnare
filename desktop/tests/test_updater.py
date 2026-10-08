"""Finding a newer release and replacing the app with it."""

import hashlib
import io
import os

import pytest

from leoslyssnare import updater


def release(tag, *names, draft=False, prerelease=False):
    return {"tag_name": tag, "draft": draft, "prerelease": prerelease,
            "html_url": f"https://github.com/x/y/releases/tag/{tag}",
            "assets": [{"name": n, "browser_download_url": f"https://example.com/{n}", "size": 3} for n in names]}


def test_versions_compare_by_number_not_text():
    assert updater.is_newer("0.1.10", "0.1.9")
    assert updater.is_newer("0.2.0", "0.1.9")
    assert not updater.is_newer("0.1.2", "0.1.2")
    assert not updater.is_newer("0.1.1", "0.1.2")


def test_latest_release_skips_drafts_prereleases_and_other_tags():
    found = updater.latest_release([
        release("desktop-v0.3.0", draft=True),
        release("desktop-v0.2.5", prerelease=True),
        release("mac-v9.9.9"),
        release("desktop-v0.2.0", "Leos_Lyssnare-0.2.0-windows-x64-setup.exe"),
        release("desktop-v0.1.10"),
    ])
    assert found.version == "0.2.0"
    assert found.assets[0].name.endswith("-setup.exe")
    assert updater.latest_release([]) is None


def test_asset_matches_how_the_app_is_installed():
    found = updater.latest_release([release(
        "desktop-v0.2.0", "Leos_Lyssnare-0.2.0-windows-x64-setup.exe", "Leos_Lyssnare-0.2.0-windows-x64.zip",
        "Leos_Lyssnare-x86_64.AppImage", "Leos_Lyssnare-0.2.0-macos-arm64.zip")])
    assert updater.pick_asset(found, updater.WINDOWS_INSTALLER).name.endswith("-setup.exe")
    assert updater.pick_asset(found, updater.APPIMAGE).name.endswith(".AppImage")
    assert updater.pick_asset(found, None) is None  # portable zip: sent to the download page


class FakeResponse(io.BytesIO):
    headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def test_download_checks_the_checksum_and_cleans_up(monkeypatch, tmp_path):
    data = b"abc"
    monkeypatch.setattr(updater, "_open", lambda url, timeout: FakeResponse(data))
    target = str(tmp_path / "ok")
    updater.download(updater.Asset("a", "u", 3, hashlib.sha256(data).hexdigest()), target, lambda f: None, lambda: False)
    assert open(target, "rb").read() == data

    bad = str(tmp_path / "bad")
    with pytest.raises(updater.UpdateError):
        updater.download(updater.Asset("a", "u", 3, "0" * 64), bad, lambda f: None, lambda: False)
    assert not os.path.exists(bad)


def test_replacing_the_appimage(monkeypatch, tmp_path):
    old, new = tmp_path / "App.AppImage", tmp_path / ".part"
    old.write_bytes(b"old")
    new.write_bytes(b"new")
    monkeypatch.setenv("APPIMAGE", str(old))
    assert updater.replace_appimage(str(new)) == str(old)
    assert old.read_bytes() == b"new" and os.access(old, os.X_OK) and not new.exists()
