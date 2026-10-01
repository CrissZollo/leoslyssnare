"""Records the microphone to a single audio file in the platform's usual format:
MP3 on Windows, Ogg (Opus) on Linux.

The microphone stream stays open for the whole recording. While paused, the
incoming audio is simply dropped, so the finished file contains every
unpaused part back to back and none of the paused time.
"""

from __future__ import annotations

import math
import queue
import sys
import threading
from dataclasses import dataclass

import numpy as np

from . import keepawake, paths

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

    @property
    def elapsed(self) -> float:
        """Recorded length in seconds, excluding paused time."""
        return self._frames / self._sample_rate

    @property
    def level(self) -> float:
        """Input level in 0...1 for the meter."""
        return self._level if self.state == RECORDING else 0.0

    def start(self) -> None:
        if self.state != IDLE:
            return
        import av  # noqa: F401  (fail early if the encoder is missing)

        try:
            import sounddevice as sd
        except OSError as error:  # PortAudio missing
            raise RecorderError(
                "The audio library PortAudio couldn't be loaded, so recording isn't possible. "
                "On Linux, install the package “libportaudio2”."
            ) from error

        try:
            device = sd.query_devices(kind="input")
            self._sample_rate = int(device["default_samplerate"]) or 44_100
        except Exception as error:
            raise RecorderError(f"No microphone was found. {_microphone_hint()}") from error

        self.path = paths.new_recording_path(self.format.extension)
        self._frames = 0
        self._level = 0.0
        self._writer_error = None
        self._queue = queue.Queue()
        try:
            self._stream = sd.InputStream(
                samplerate=self._sample_rate,
                channels=1,
                dtype="float32",
                callback=self._on_audio,
            )
        except Exception as error:
            raise RecorderError(f"Couldn't start recording: {error}. {_microphone_hint()}") from error

        self._writer = threading.Thread(
            target=self._write, args=(self.path, self._sample_rate, self._queue), daemon=True
        )
        self._writer.start()
        self.state = RECORDING
        self._stream.start()
        keepawake.acquire()

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
        self.state = IDLE
        try:
            if self._stream is not None:
                self._stream.stop()
                self._stream.close()
        finally:
            self._stream = None
            self._queue.put(None)
            if self._writer is not None:
                self._writer.join()
                self._writer = None
            keepawake.release()
        if self._writer_error is not None:
            raise RecorderError(f"The recording couldn't be saved: {self._writer_error}")
        return self.path

    # Runs on PortAudio's audio thread: keep it short.
    def _on_audio(self, indata, frames, time, status) -> None:  # noqa: ARG002
        if self.state != RECORDING:
            return
        samples = indata[:, 0].copy()
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
