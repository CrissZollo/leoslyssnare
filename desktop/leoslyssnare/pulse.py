"""Linux sound through PulseAudio or PipeWire (which speaks the same protocol):
lists the microphones and the apps that are playing sound, and captures
either one. See sources.py for how it's used.

It uses the sound server's own command-line tools, pactl and parec, which
every desktop with PulseAudio or PipeWire has. Capturing an app means
listening to its playback streams, so the app sounds exactly as it does in
the speakers, and nothing about its playback changes.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading

import numpy as np

from .paths import system_env
from .sources import ALL_SOUND, Application, Microphone

RATE = 48_000

def _tool(name: str) -> str | None:
    return shutil.which(name) if sys.platform.startswith("linux") else None


def available() -> bool:
    """Whether microphones and apps can be chosen here."""
    return bool(_tool("pactl") and _tool("parec")) and _pactl("info") is not None


def can_record_apps() -> bool:
    return True


def _pactl(*args: str) -> str | None:
    env = system_env()
    env["LC_ALL"] = "C"  # the output is translated otherwise
    try:
        result = subprocess.run([_tool("pactl") or "pactl", *args], env=env, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


def _blocks(text: str, header: str) -> list[tuple[int, dict[str, str], dict[str, str]]]:
    """Splits pactl's `list` output into (index, fields, properties) per object."""
    blocks: list[tuple[int, dict[str, str], dict[str, str]]] = []
    for line in text.splitlines():
        if line.startswith(header + " #"):
            blocks.append((int(line.split("#", 1)[1]), {}, {}))
        elif blocks and line.startswith("\t\t") and " = " in line:
            key, _, value = line.strip().partition(" = ")
            blocks[-1][2][key] = value.strip('"')
        elif blocks and line.startswith("\t") and not line.startswith("\t\t") and ":" in line:
            key, _, value = line.strip().partition(":")
            blocks[-1][1][key] = value.strip()
    return blocks


def default_microphone() -> str | None:
    for line in (_pactl("info") or "").splitlines():
        if line.startswith("Default Source:"):
            return line.partition(":")[2].strip() or None
    return None


def microphones() -> list[Microphone]:
    """Every input except the monitors, which play back what an output plays."""
    found = []
    for _, fields, properties in _blocks(_pactl("list", "sources") or "", "Source"):
        if fields.get("Monitor of Sink", "n/a") != "n/a" or properties.get("device.class") == "monitor":
            continue
        if "Name" in fields:
            found.append(Microphone(fields["Name"], fields.get("Description") or fields["Name"]))
    return found


def _app_key(properties: dict[str, str]) -> str | None:
    return properties.get("application.process.binary") or properties.get("application.name")


def _app_streams(text: str) -> list[tuple[int, dict[str, str]]]:
    """The playback streams of other programs than this one."""
    own = str(os.getpid())
    return [(index, properties) for index, _, properties in _blocks(text, "Sink Input")
            if _app_key(properties) and properties.get("application.process.id") != own]


def applications() -> list[Application]:
    """The apps playing sound right now. Browsers and meeting apps only show
    up once they play something, for example once a call has started."""
    found: dict[str, Application] = {}
    for _, properties in _app_streams(_pactl("list", "sink-inputs") or ""):
        key = _app_key(properties)
        name = properties.get("application.name") or key
        binary = properties.get("application.process.binary")
        # Electron apps call themselves "Chromium": tell them apart by their program.
        if binary and binary.lower() not in name.lower():
            name = f"{name} ({binary})"
        found.setdefault(key, Application(key, name))
    return sorted(found.values(), key=lambda app: app.name.lower())


class Capture:
    """One parec process delivering mono float samples to a callback, on its
    own thread."""

    def __init__(self, source_args: list[str], on_audio, rate: int = RATE) -> None:
        self._args = source_args
        self._on_audio = on_audio
        self._rate = rate
        self._process: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self.started = threading.Event()  # the first audio has arrived
        self.error = ""

    def start(self) -> None:
        self._process = subprocess.Popen(
            [_tool("parec") or "parec", *self._args, "--raw", "--format=float32le", f"--rate={self._rate}",
             "--channels=1", "--latency-msec=20", "--client-name=Leos Lyssnare"],
            env=system_env(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._thread.start()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def stop(self) -> None:
        if self._process is not None and self._process.poll() is None:
            self._process.terminate()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _read(self) -> None:
        process = self._process
        fd = process.stdout.fileno()
        leftover = b""
        while data := os.read(fd, 16_384):
            data = leftover + data
            usable = len(data) - len(data) % 4
            leftover = data[usable:]
            if usable:
                self._on_audio(np.frombuffer(data[:usable], dtype=np.float32).copy())
                self.started.set()
        process.wait()
        if process.returncode not in (0, -15):  # -15: stopped by us
            self.error = (process.stderr.read().decode(errors="replace").strip()
                          or f"parec exited with code {process.returncode}")
        process.stdout.close()
        process.stderr.close()
        self.started.set()


def find_streams(target: str) -> dict[int, list[str]] | None:
    """The streams to capture for an app, or everything the computer plays."""
    if target == ALL_SOUND:
        return {-1: ["--device=@DEFAULT_MONITOR@"]}
    text = _pactl("list", "sink-inputs")
    if text is None:
        return None
    return {index: [f"--monitor-stream={index}"] for index, properties in _app_streams(text)
            if _app_key(properties) == target}


def open_stream(spec: list[str], rate: int, on_audio) -> Capture:
    return Capture(spec, on_audio, rate)
