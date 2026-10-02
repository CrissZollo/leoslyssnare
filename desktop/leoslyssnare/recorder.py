"""Records the microphone to a single audio file in the platform's usual format:
MP3 on Windows, Ogg (Opus) on Linux.

The microphone stream stays open for the whole recording. While paused, the
incoming audio is simply dropped, so the finished file contains every
unpaused part back to back and none of the paused time.

The microphone can be chosen, and the sound of another app (the meeting app,
say) can be mixed in, so both sides of a call end up in the recording; see
sources.py. On Linux that goes through PulseAudio or PipeWire (pulse.py), on
Windows through WASAPI (wasapi.py).
"""

from __future__ import annotations

import math
import os
import queue
import sys
import threading
from dataclasses import dataclass

import numpy as np

from . import keepawake, paths, pulse, sources

IDLE, RECORDING, PAUSED = "idle", "recording", "paused"


@dataclass(frozen=True)
class RecordingFormat:
    extension: str
    container: str
    codec: str
    bit_rate: int
    # Fixed encoder sample rate, or None to keep the microphone's own rate.
    sample_rate: int | None = None


# Both formats are streamable, so if the app or computer crashes mid-meeting,
# everything recorded up to that point is still playable.
MP3 = RecordingFormat(".mp3", "mp3", "libmp3lame", 128_000)
# Opus only supports 48 kHz (among speech-friendly rates); the encoder resamples.
OGG_OPUS = RecordingFormat(".ogg", "ogg", "libopus", 64_000, sample_rate=48_000)


def platform_format() -> RecordingFormat:
    """MP3 plays in every Windows app; Ogg is the native format on Linux desktops
    (GNOME Sound Recorder, Audacity, browsers and media players all handle it)."""
    return MP3 if sys.platform == "win32" else OGG_OPUS


class RecorderError(Exception):
    pass


def _microphone_hint() -> str:
    if sys.platform == "win32":
        return ("Check that a microphone is connected and that apps may use it under "
                "Settings › Privacy & security › Microphone.")
    return "Check that a microphone is connected and selected as the input device in your sound settings."


def _apps_hint() -> str:
    if sys.platform == "win32":
        return "Recording another app's sound needs Windows 11."
    return ("Recording another app's sound needs PulseAudio or PipeWire and their tools pactl and "
            "parec. On Debian and Ubuntu they're in the package “pulseaudio-utils”.")


class AudioRecorder:
    def __init__(self, recording_format: RecordingFormat | None = None) -> None:
        self.format = recording_format or platform_format()
        self.state = IDLE
        self.path: str | None = None
        self._stream = None
        self._queue: queue.Queue[np.ndarray | None] = queue.Queue()
        self._writer: threading.Thread | None = None
        self._writer_error: Exception | None = None
        self._sample_rate = 44_100
        self._frames = 0
        self._level = 0.0
        # The microphone, as a sources.Microphone name (None: the default one),
        # and the app whose sound is mixed in (an Application key,
        # sources.ALL_SOUND, or None for none).
        self.microphone: str | None = None
        self.application: str | None = None
        self._capture: pulse.Capture | None = None
        self._app: sources.AppAudio | None = None

    @property
    def elapsed(self) -> float:
        """Recorded length in seconds, excluding paused time."""
        return self._frames / self._sample_rate

    @property
    def level(self) -> float:
        """Input level in 0...1 for the meter."""
        return self._level if self.state == RECORDING else 0.0

    @property
    def hearing_app(self) -> bool:
        """Whether the chosen app has sound streams being recorded right now."""
        return self._app is not None and self._app.stream_count > 0

    def start(self) -> None:
        if self.state != IDLE:
            return
        import av  # noqa: F401  (fail early if the encoder is missing)

        use_pulse = pulse.available()
        if self.application and not sources.can_record_apps():
            raise RecorderError(_apps_hint())
        if use_pulse:
            self._sample_rate = pulse.RATE
        else:
            self._sample_rate = self._open_portaudio()

        self.path = paths.new_recording_path(self.format.extension)
        self._frames = 0
        self._level = 0.0
        self._writer_error = None
        self._queue = queue.Queue()
        self._writer = threading.Thread(
            target=self._write, args=(self.path, self._sample_rate, self._queue), daemon=True
        )
        self._writer.start()
        self.state = RECORDING
        keepawake.acquire()
        try:
            if self.application:
                # First, so the app's sound is already flowing when the microphone's arrives.
                self._app = sources.app_audio(self.application, self._sample_rate)
                self._app.start()
            if use_pulse:
                self._start_pulse()
            else:
                self._stream.start()
        except Exception as error:
            self._close()
            if os.path.exists(self.path):  # nothing was recorded
                os.remove(self.path)
            if isinstance(error, RecorderError):
                raise
            raise RecorderError(f"Couldn't start recording: {error}. {_microphone_hint()}") from error

    def _open_portaudio(self) -> int:
        """Opens the microphone with PortAudio and returns its sample rate."""
        try:
            import sounddevice as sd
        except OSError as error:  # PortAudio missing
            raise RecorderError(
                "The audio library PortAudio couldn't be loaded, so recording isn't possible. "
                "On Linux, install the package “libportaudio2”."
            ) from error

        device = settings = None
        if self.microphone and sys.platform == "win32":
            from . import wasapi

            # One that isn't connected any more falls back to the default.
            device = wasapi.input_device(self.microphone)
            if device is not None:
                # Lets Windows convert to mono, whatever the microphone delivers.
                settings = sd.WasapiSettings(auto_convert=True)
        try:
            info = sd.query_devices(device, kind="input")
            sample_rate = int(info["default_samplerate"]) or 44_100
        except Exception as error:
            raise RecorderError(f"No microphone was found. {_microphone_hint()}") from error

        try:
            self._stream = sd.InputStream(
                device=device,
                samplerate=sample_rate,
                channels=1,
                dtype="float32",
                callback=self._on_portaudio,
                extra_settings=settings,
            )
        except Exception as error:
            raise RecorderError(f"Couldn't start recording: {error}. {_microphone_hint()}") from error
        return sample_rate

    def _start_pulse(self) -> None:
        self._capture = pulse.Capture([f"--device={self.microphone or '@DEFAULT_SOURCE@'}"], self._on_audio)
        self._capture.start()
        # A microphone that can't be opened is reported now rather than when recording stops.
        self._capture.started.wait(3)
        if self._capture.error:
            raise RecorderError(f"The microphone couldn't be opened ({self._capture.error}). {_microphone_hint()}")

    def pause(self) -> None:
        if self.state == RECORDING:
            self.state = PAUSED

    def resume(self) -> None:
        if self.state == PAUSED:
            self.state = RECORDING

    def stop(self) -> str | None:
        """Finishes the file and returns its location."""
        if self.state == IDLE:
            return None
        self._close()
        if self._writer_error is not None:
            raise RecorderError(f"The recording couldn't be saved: {self._writer_error}")
        return self.path

    def _close(self) -> None:
        self.state = IDLE
        try:
            if self._stream is not None:
                self._stream.stop()
                self._stream.close()
        finally:
            self._stream = None
            for source in (self._capture, self._app):
                if source is not None:
                    source.stop()
            self._capture = self._app = None
            self._queue.put(None)
            if self._writer is not None:
                self._writer.join()
                self._writer = None
            keepawake.release()

    # Runs on PortAudio's audio thread: keep it short.
    def _on_portaudio(self, indata, frames, time, status) -> None:  # noqa: ARG002
        self._on_audio(indata[:, 0].copy())

    # Runs on the microphone's audio thread: keep it short.
    def _on_audio(self, samples: np.ndarray) -> None:
        app = self._app
        if self.state != RECORDING:
            if app is not None:
                app.take(len(samples))  # the app's sound while paused is left out too
            return
        if app is not None:
            samples = np.clip(samples + app.take(len(samples)), -1.0, 1.0)
        self._queue.put(samples)
        self._frames += len(samples)
        rms = float(np.sqrt(np.mean(samples * samples))) if len(samples) else 0.0
        decibels = 20 * math.log10(rms) if rms > 0 else -160.0
        self._level = max(0.0, min(1.0, (decibels + 50) / 50))

    def _write(self, path: str, sample_rate: int, chunks: queue.Queue) -> None:
        import av

        try:
            fmt = self.format
            container = av.open(path, mode="w", format=fmt.container)
            stream = container.add_stream(fmt.codec, rate=fmt.sample_rate or sample_rate, layout="mono")
            stream.bit_rate = fmt.bit_rate
            pts = 0
            while (samples := chunks.get()) is not None:
                frame = av.AudioFrame.from_ndarray(samples.reshape(1, -1), format="flt", layout="mono")
                frame.sample_rate = sample_rate
                frame.pts = pts
                pts += samples.shape[0]
                for packet in stream.encode(frame):
                    container.mux(packet)
            for packet in stream.encode(None):
                container.mux(packet)
            container.close()
        except Exception as error:  # reported from stop()
            self._writer_error = error
            # Keep draining so the audio callback never blocks.
            while chunks.get() is not None:
                pass
