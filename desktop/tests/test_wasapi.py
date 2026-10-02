"""Checks the Windows capture code's pure parts on any system: the layouts
Windows expects, finding the app a sound-playing process belongs to, and
the COM callback object."""

import ctypes

from leoslyssnare import sources, wasapi


def test_layouts_match_windows():
    assert ctypes.sizeof(wasapi.GUID) == 16
    assert bytes(wasapi.IID_IAudioClient).hex() == "4cadb91cfadb324cb178c2f568a703b2"
    assert ctypes.sizeof(wasapi.PROPVARIANT) == 8 + ctypes.sizeof(ctypes.c_void_p) * 2
    assert wasapi.PROPVARIANT.blob.offset == 8
    assert ctypes.sizeof(wasapi.AUDIOCLIENT_ACTIVATION_PARAMS) == 12
    assert ctypes.sizeof(wasapi.WAVEFORMATEX) == 18


# pid: (parent, program). Teams plays through WebView2 helpers; Chrome
# through its audio service; Zoom was started from a terminal.
PROCESSES = {
    4: (0, "System"), 900: (4, "svchost.exe"), 1000: (900, "explorer.exe"),
    2000: (1000, "ms-teams.exe"), 2100: (2000, "msedgewebview2.exe"), 2200: (2100, "msedgewebview2.exe"),
    3000: (1000, "chrome.exe"), 3100: (3000, "chrome.exe"),
    4000: (1000, "WindowsTerminal.exe"), 4100: (4000, "pwsh.exe"), 4200: (4100, "Zoom.exe"),
    5000: (6666, "orphan.exe"),
}


def started_in_order(pid):
    return pid


def test_sound_from_a_helper_belongs_to_its_app():
    def root(pid):
        return wasapi._app_root(pid, PROCESSES, started_in_order)

    assert root(2200) == 2000
    assert root(3100) == 3000
    assert root(4200) == 4200  # not the terminal it was started from
    assert root(5000) == 5000  # its parent has exited
    # A parent pid that was reused by a program started later isn't the parent.
    assert wasapi._app_root(2100, PROCESSES, lambda pid: 9999 if pid == 2000 else pid) == 2100


def test_capture_follows_each_copy_of_the_app(monkeypatch):
    processes = {**PROCESSES, 7000: (1000, "chrome.exe"), 7100: (7000, "chrome.exe")}
    monkeypatch.setattr(wasapi, "_processes", lambda: processes)
    assert wasapi.find_streams("chrome.exe") == {3000: (3000, True), 7000: (7000, True)}
    assert wasapi.find_streams("ms-teams.exe") == {2000: (2000, True)}
    import os

    assert wasapi.find_streams(sources.ALL_SOUND) == {"all": (os.getpid(), False)}


def test_completion_handler_answers_like_a_com_object():
    handler = wasapi._CompletionHandler()
    this = handler.pointer.value
    vtable = handler._vtable
    out = ctypes.c_void_p()
    for iid in (wasapi.IID_IUnknown, wasapi.IID_IAgileObject, wasapi.IID_IActivateAudioInterfaceCompletionHandler):
        assert vtable.QueryInterface(this, ctypes.byref(iid), ctypes.byref(out)) == 0
        assert out.value == this
    assert vtable.QueryInterface(this, ctypes.byref(wasapi.IID_IAudioClient), ctypes.byref(out)) == wasapi.E_NOINTERFACE
    assert not out.value
    assert not handler.done.is_set()
    assert vtable.ActivateCompleted(this, None) == 0
    assert handler.done.is_set()
