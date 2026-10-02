"""Windows sound through WASAPI: lists the microphones and the apps that are
playing sound, and captures either one. See sources.py for how it's used.

Microphones are PortAudio's WASAPI devices. An app's sound is captured with
"process loopback" (Windows 11 and later): Windows hands over a copy of
everything the app and the processes it started play, exactly as it goes to
the speakers, without changing anything about its playback. The COM calls go
through ctypes, so nothing has to be compiled.
"""

from __future__ import annotations

import ctypes
import os
import sys
import threading
import uuid
from ctypes import POINTER, byref, c_int32, c_int64, c_uint16, c_uint32, c_void_p

import numpy as np

from .sources import ALL_SOUND, Application, Microphone

# Fixed-size types, so the layouts below are the same on every platform the tests run on.
DWORD, WORD, UINT32, HRESULT = c_uint32, c_uint16, c_uint32, c_int32
_FUNCTYPE = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)

# Process loopback arrived in Windows 11 (build 22000; Server 2022 is 20348).
FIRST_BUILD_WITH_APP_CAPTURE = 20348


class GUID(ctypes.Structure):
    _fields_ = [("Data1", c_uint32), ("Data2", c_uint16), ("Data3", c_uint16), ("Data4", ctypes.c_ubyte * 8)]


def _guid(text: str) -> GUID:
    return GUID.from_buffer_copy(uuid.UUID(text).bytes_le)


CLSID_MMDeviceEnumerator = _guid("BCDE0395-E52F-467C-8E3D-C4579291692E")
IID_IUnknown = _guid("00000000-0000-0000-C000-000000000046")
IID_IAgileObject = _guid("94EA2B94-E9CC-49E0-C0FF-EE64CA8F5B90")
IID_IMMDeviceEnumerator = _guid("A95664D2-9614-4F35-A746-DE8DB63617E6")
IID_IAudioSessionManager2 = _guid("77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F")
IID_IAudioSessionControl2 = _guid("BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D")
IID_IAudioClient = _guid("1CB9AD4C-DBFA-4C32-B178-C2F568A703B2")
IID_IAudioCaptureClient = _guid("C8ADBD64-E71E-48A0-A4DE-185C395CD317")
IID_IActivateAudioInterfaceCompletionHandler = _guid("41D949AB-9862-444A-80F6-C261334DA5EB")

CLSCTX_ALL = 0x17
COINIT_MULTITHREADED = 0x0
E_NOINTERFACE = -0x7FFFBFFE  # 0x80004002
eRender, DEVICE_STATE_ACTIVE = 0, 0x1
AudioSessionStateExpired = 2
VT_BLOB = 65
AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK = 1
PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE = 0
PROCESS_LOOPBACK_MODE_EXCLUDE_TARGET_PROCESS_TREE = 1
AUDCLNT_SHAREMODE_SHARED = 0
AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
AUDCLNT_STREAMFLAGS_EVENTCALLBACK = 0x00040000
AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY = 0x08000000
AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM = 0x80000000
AUDCLNT_BUFFERFLAGS_SILENT = 0x2
WAVE_FORMAT_PCM = 1
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TH32CS_SNAPPROCESS = 0x2
VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK = "VAD\\Process_Loopback"


class BLOB(ctypes.Structure):
    _fields_ = [("cbSize", c_uint32), ("pBlobData", c_void_p)]


class PROPVARIANT(ctypes.Structure):
    # Only the BLOB case is needed: vt, three reserved words, then the value.
    _fields_ = [("vt", c_uint16), ("reserved1", c_uint16), ("reserved2", c_uint16), ("reserved3", c_uint16),
                ("blob", BLOB)]


class AUDIOCLIENT_ACTIVATION_PARAMS(ctypes.Structure):
    _fields_ = [("ActivationType", c_int32), ("TargetProcessId", c_uint32), ("ProcessLoopbackMode", c_int32)]


class WAVEFORMATEX(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("wFormatTag", WORD), ("nChannels", WORD), ("nSamplesPerSec", DWORD), ("nAvgBytesPerSec", DWORD),
                ("nBlockAlign", WORD), ("wBitsPerSample", WORD), ("cbSize", WORD)]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", DWORD), ("cntUsage", DWORD), ("th32ProcessID", DWORD), ("th32DefaultHeapID", c_void_p),
                ("th32ModuleID", DWORD), ("cntThreads", DWORD), ("th32ParentProcessID", DWORD),
                ("pcPriClassBase", c_int32), ("dwFlags", DWORD), ("szExeFile", ctypes.c_wchar * 260)]


# MARK: - COM plumbing

def _method(obj: c_void_p, index: int, *argtypes):
    """Method number `index` of a COM object's vtable. Failed HRESULTs raise OSError."""
    vtable = ctypes.cast(obj, POINTER(POINTER(c_void_p)))[0]
    return _FUNCTYPE(ctypes.HRESULT, c_void_p, *argtypes)(vtable[index])


def _release(obj: c_void_p | None) -> None:
    if obj:
        vtable = ctypes.cast(obj, POINTER(POINTER(c_void_p)))[0]
        _FUNCTYPE(c_uint32, c_void_p)(vtable[2])(obj)


def _query(obj: c_void_p, iid: GUID) -> c_void_p:
    out = c_void_p()
    _method(obj, 0, POINTER(GUID), POINTER(c_void_p))(obj, byref(iid), byref(out))
    return out


class _Com:
    """Joins COM for a block on this thread. On a thread that's already in
    COM in another mode (Qt's main thread), that's kept, which works too."""

    def __enter__(self):
        result = ctypes.windll.ole32.CoInitializeEx(None, COINIT_MULTITHREADED)
        self._joined = result in (0, 1)  # S_OK, S_FALSE
        return self

    def __exit__(self, *exc):
        if self._joined:
            ctypes.windll.ole32.CoUninitialize()


# MARK: - What's available

def _wasapi(sd):
    for index, api in enumerate(sd.query_hostapis()):
        if "WASAPI" in api["name"]:
            return index, api
    return None, None


def available() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import sounddevice as sd

        return _wasapi(sd)[0] is not None
    except Exception:
        return False


def can_record_apps() -> bool:
    return sys.platform == "win32" and sys.getwindowsversion().build >= FIRST_BUILD_WITH_APP_CAPTURE


def _input_devices(sd) -> list[tuple[int, str]]:
    index, _ = _wasapi(sd)
    return [(i, device["name"]) for i, device in enumerate(sd.query_devices())
            if device["hostapi"] == index and device["max_input_channels"] > 0
            and "[Loopback]" not in device["name"]]


def microphones() -> list[Microphone]:
    import sounddevice as sd

    return [Microphone(name, name) for _, name in _input_devices(sd)]


def default_microphone() -> str | None:
    import sounddevice as sd

    _, api = _wasapi(sd)
    if api is None or api["default_input_device"] < 0:
        return None
    return sd.query_devices(api["default_input_device"])["name"]


def input_device(name: str) -> int | None:
    """PortAudio's number for a microphone, or None when it isn't connected."""
    import sounddevice as sd

    return next((index for index, device_name in _input_devices(sd) if device_name == name), None)


# MARK: - Apps

def _processes() -> dict[int, tuple[int, str]]:
    """Every process, as pid: (parent pid, program file name)."""
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateToolhelp32Snapshot.restype = c_void_p
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot in (None, c_void_p(-1).value):
        raise ctypes.WinError()
    found = {}
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(entry)
    try:
        more = kernel32.Process32FirstW(c_void_p(snapshot), byref(entry))
        while more:
            found[entry.th32ProcessID] = (entry.th32ParentProcessID, entry.szExeFile)
            more = kernel32.Process32NextW(c_void_p(snapshot), byref(entry))
    finally:
        kernel32.CloseHandle(c_void_p(snapshot))
    return found


def _open_process(pid: int):
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.restype = c_void_p
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    return c_void_p(handle) if handle else None


def _started(pid: int) -> int | None:
    handle = _open_process(pid)
    if handle is None:
        return None
    try:
        times = [c_int64() for _ in range(4)]
        if not ctypes.windll.kernel32.GetProcessTimes(handle, *[byref(t) for t in times]):
            return None
        return times[0].value
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def _image_path(pid: int) -> str | None:
    handle = _open_process(pid)
    if handle is None:
        return None
    try:
        size = c_uint32(1024)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not ctypes.windll.kernel32.QueryFullProcessImageNameW(handle, 0, buffer, byref(size)):
            return None
        return buffer.value
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def _file_description(path: str) -> str | None:
    """The name a program gives itself ("Microsoft Teams" for ms-teams.exe)."""
    version = ctypes.windll.version
    size = version.GetFileVersionInfoSizeW(path, None)
    if not size:
        return None
    data = ctypes.create_string_buffer(size)
    if not version.GetFileVersionInfoW(path, 0, size, data):
        return None
    pointer, length = c_void_p(), c_uint32()
    if not version.VerQueryValueW(data, "\\VarFileInfo\\Translation", byref(pointer), byref(length)) or not length.value:
        return None
    language, codepage = ctypes.cast(pointer, POINTER(c_uint16 * 2)).contents
    key = f"\\StringFileInfo\\{language:04x}{codepage:04x}\\FileDescription"
    if not version.VerQueryValueW(data, key, byref(pointer), byref(length)) or not length.value:
        return None
    return ctypes.wstring_at(pointer.value).strip() or None


# Where walking up from a sound-playing process to its app stops: the parents
# apps are started from, rather than apps themselves.
_LAUNCHERS = {"explorer.exe", "svchost.exe", "sihost.exe", "services.exe", "wininit.exe", "winlogon.exe",
              "userinit.exe", "runtimebroker.exe", "cmd.exe", "powershell.exe", "pwsh.exe",
              "windowsterminal.exe", "openconsole.exe", "conhost.exe"}


def _app_root(pid: int, processes: dict[int, tuple[int, str]], started=None) -> int:
    """The app a process belongs to. Browsers and Electron or WebView2 apps
    (the new Teams, for one) play sound from a helper process, so this walks
    up the parents to the top process that isn't a launcher. A parent that
    started after its child is a different program that got a reused pid."""
    started = started or _started
    seen = {pid}
    while True:
        parent = processes[pid][0]
        if (parent not in processes or parent in seen or parent == 0
                or processes[parent][1].lower() in _LAUNCHERS):
            return pid
        child_start, parent_start = started(pid), started(parent)
        if child_start is None or parent_start is None or parent_start > child_start:
            return pid
        seen.add(parent)
        pid = parent


def _session_pids() -> set[int]:
    """The processes with sound sessions on any speaker or headset, other
    than ones that have ended and the Windows system sounds."""
    pids: set[int] = set()
    with _Com():
        enumerator = c_void_p()
        ctypes.oledll.ole32.CoCreateInstance(byref(CLSID_MMDeviceEnumerator), None, CLSCTX_ALL,
                                             byref(IID_IMMDeviceEnumerator), byref(enumerator))
        held = [enumerator]
        try:
            collection = c_void_p()
            _method(enumerator, 3, c_int32, c_uint32, POINTER(c_void_p))(enumerator, eRender, DEVICE_STATE_ACTIVE,
                                                                         byref(collection))
            held.append(collection)
            count = c_uint32()
            _method(collection, 3, POINTER(c_uint32))(collection, byref(count))
            for i in range(count.value):
                device = c_void_p()
                _method(collection, 4, c_uint32, POINTER(c_void_p))(collection, i, byref(device))
                held.append(device)
                manager = c_void_p()
                _method(device, 3, POINTER(GUID), c_uint32, c_void_p, POINTER(c_void_p))(
                    device, byref(IID_IAudioSessionManager2), CLSCTX_ALL, None, byref(manager))
                held.append(manager)
                sessions = c_void_p()
                _method(manager, 5, POINTER(c_void_p))(manager, byref(sessions))
                held.append(sessions)
                total = c_int32()
                _method(sessions, 3, POINTER(c_int32))(sessions, byref(total))
                for j in range(total.value):
                    control = c_void_p()
                    _method(sessions, 4, c_int32, POINTER(c_void_p))(sessions, j, byref(control))
                    held.append(control)
                    state = c_int32()
                    _method(control, 3, POINTER(c_int32))(control, byref(state))
                    if state.value == AudioSessionStateExpired:
                        continue
                    control2 = _query(control, IID_IAudioSessionControl2)
                    held.append(control2)
                    if _method(control2, 15)(control2) == 0:  # S_OK: the system sounds
                        continue
                    pid = c_uint32()
                    _method(control2, 14, POINTER(c_uint32))(control2, byref(pid))
                    if pid.value:
                        pids.add(pid.value)
        finally:
            for obj in reversed(held):
                _release(obj)
    return pids


def applications() -> list[Application]:
    try:
        processes = _processes()
        pids = _session_pids()
    except OSError:
        return []
    own = os.getpid()
    found: dict[str, Application] = {}
    for pid in pids - {own}:
        if pid not in processes:
            continue
        root = _app_root(pid, processes)
        if root == own:
            continue
        key = processes[root][1].lower()
        if key not in found:
            path = _image_path(root)
            name = (_file_description(path) if path else None) or os.path.splitext(processes[root][1])[0]
            found[key] = Application(key, name)
    return sorted(found.values(), key=lambda app: app.name.lower())


def find_streams(target: str) -> dict | None:
    """What to capture: for an app, each copy of its program that wasn't
    started by itself (so helper processes come along with their app), as
    pid: (pid, include). For ALL_SOUND, everything except this app."""
    if target == ALL_SOUND:
        own = os.getpid()
        return {"all": (own, False)}
    processes = _processes()
    return {pid: (pid, True) for pid, (parent, exe) in processes.items()
            if exe.lower() == target and processes.get(parent, (0, ""))[1].lower() != target}


def open_stream(spec: tuple[int, bool], rate: int, on_audio) -> ProcessLoopback:
    pid, include = spec
    return ProcessLoopback(pid, include, rate, on_audio)


# MARK: - Capture

_QueryInterface = _FUNCTYPE(c_int32, c_void_p, POINTER(GUID), POINTER(c_void_p))
_AddRef = _FUNCTYPE(c_uint32, c_void_p)
_ActivateCompleted = _FUNCTYPE(c_int32, c_void_p, c_void_p)


class _HandlerVTable(ctypes.Structure):
    _fields_ = [("QueryInterface", _QueryInterface), ("AddRef", _AddRef), ("Release", _AddRef),
                ("ActivateCompleted", _ActivateCompleted)]


class _HandlerObject(ctypes.Structure):
    _fields_ = [("vtable", POINTER(_HandlerVTable))]


class _CompletionHandler:
    """The IActivateAudioInterfaceCompletionHandler that Windows calls once
    the app's sound is ready to be captured. It has to say it's agile (usable
    from any thread), or activation fails. Python keeps it alive, so counting
    references isn't needed."""

    _accepted = {bytes(IID_IUnknown), bytes(IID_IAgileObject), bytes(IID_IActivateAudioInterfaceCompletionHandler)}

    def __init__(self) -> None:
        self.done = threading.Event()
        self._vtable = _HandlerVTable(_QueryInterface(self._query), _AddRef(self._count), _AddRef(self._count),
                                      _ActivateCompleted(self._completed))
        self._object = _HandlerObject(ctypes.pointer(self._vtable))
        self.pointer = ctypes.cast(ctypes.pointer(self._object), c_void_p)

    def _query(self, this, iid, out):
        if bytes(iid.contents) in self._accepted:
            out[0] = this
            return 0
        out[0] = None
        return E_NOINTERFACE

    def _count(self, this):
        return 1

    def _completed(self, this, operation):
        self.done.set()
        return 0


class ProcessLoopback:
    """Captures what one process and the processes it started play (or, with
    include=False, everything except them), as mono float samples at `rate`,
    delivered to a callback from its own thread."""

    def __init__(self, pid: int, include: bool, rate: int, on_audio) -> None:
        self.pid = pid
        self.include = include
        self.rate = rate
        self._on_audio = on_audio
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._stopping = threading.Event()
        self._handler = _CompletionHandler()
        self.error = ""

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(10)
        if self.error:
            raise OSError(self.error)

    def stop(self) -> None:
        self._stopping.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _activate(self) -> c_void_p:
        params = AUDIOCLIENT_ACTIVATION_PARAMS(
            AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK, self.pid,
            PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE if self.include
            else PROCESS_LOOPBACK_MODE_EXCLUDE_TARGET_PROCESS_TREE)
        variant = PROPVARIANT(vt=VT_BLOB)
        variant.blob.cbSize = ctypes.sizeof(params)
        variant.blob.pBlobData = ctypes.cast(ctypes.pointer(params), c_void_p)
        operation = c_void_p()
        activate = ctypes.oledll.mmdevapi.ActivateAudioInterfaceAsync
        activate.argtypes = [ctypes.c_wchar_p, POINTER(GUID), POINTER(PROPVARIANT), c_void_p, POINTER(c_void_p)]
        activate(VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK, byref(IID_IAudioClient), byref(variant),
                 self._handler.pointer, byref(operation))
        try:
            if not self._handler.done.wait(10):
                raise OSError("Windows didn't start capturing the app's sound")
            result, unknown = c_int32(), c_void_p()
            _method(operation, 3, POINTER(c_int32), POINTER(c_void_p))(operation, byref(result), byref(unknown))
            if result.value < 0:
                raise OSError(f"Capturing the app's sound failed (0x{result.value & 0xFFFFFFFF:08X})")
        finally:
            _release(operation)
        try:
            return _query(unknown, IID_IAudioClient)
        finally:
            _release(unknown)

    def _initialize(self, client: c_void_p) -> None:
        # 16-bit stereo, as in Microsoft's sample; Windows converts to it.
        fmt = WAVEFORMATEX(WAVE_FORMAT_PCM, 2, self.rate, self.rate * 4, 4, 16, 0)
        initialize = _method(client, 3, c_int32, c_uint32, c_int64, c_int64, POINTER(WAVEFORMATEX), c_void_p)
        flags = AUDCLNT_STREAMFLAGS_LOOPBACK | AUDCLNT_STREAMFLAGS_EVENTCALLBACK
        try:
            initialize(client, AUDCLNT_SHAREMODE_SHARED,
                       flags | AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM | AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY,
                       200_000, 0, byref(fmt), None)  # 20 ms buffer, in 100 ns units
        except OSError:
            initialize(client, AUDCLNT_SHAREMODE_SHARED, flags, 200_000, 0, byref(fmt), None)

    def _run(self) -> None:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateEventW.restype = c_void_p
        client = capture = None
        event = None
        with _Com():
            try:
                client = self._activate()
                self._initialize(client)
                event = c_void_p(kernel32.CreateEventW(None, False, False, None))
                _method(client, 13, c_void_p)(client, event)
                capture = c_void_p()
                _method(client, 14, POINTER(GUID), POINTER(c_void_p))(client, byref(IID_IAudioCaptureClient),
                                                                    byref(capture))
                _method(client, 10)(client)
            except OSError as error:
                self.error = str(error) or type(error).__name__
                self._ready.set()
                _release(capture)
                _release(client)
                if event:
                    kernel32.CloseHandle(event)
                return
            self._ready.set()
            try:
                self._pump(kernel32, event, capture)
            except OSError as error:
                self.error = str(error)
            finally:
                try:
                    _method(client, 11)(client)
                except OSError:
                    pass
                _release(capture)
                _release(client)
                kernel32.CloseHandle(event)

    def _pump(self, kernel32, event, capture) -> None:
        next_size = _method(capture, 5, POINTER(c_uint32))
        get_buffer = _method(capture, 3, POINTER(c_void_p), POINTER(c_uint32), POINTER(c_uint32), c_void_p, c_void_p)
        release_buffer = _method(capture, 4, c_uint32)
        size, data, frames, flags = c_uint32(), c_void_p(), c_uint32(), c_uint32()
        while not self._stopping.is_set():
            kernel32.WaitForSingleObject(event, 100)
            while True:
                next_size(capture, byref(size))
                if not size.value:
                    break
                get_buffer(capture, byref(data), byref(frames), byref(flags), None, None)
                count = frames.value
                if flags.value & AUDCLNT_BUFFERFLAGS_SILENT or not data.value:
                    samples = np.zeros(count, dtype=np.float32)
                else:
                    pcm = np.frombuffer(ctypes.string_at(data.value, count * 4), dtype=np.int16).reshape(-1, 2)
                    samples = pcm.mean(axis=1, dtype=np.float32) / 32768
                release_buffer(capture, count)
                if count:
                    self._on_audio(samples)
