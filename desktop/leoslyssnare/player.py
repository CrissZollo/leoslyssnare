"""Plays a recording back through the speakers, so the transcript can be
followed along with the audio.

Uses the same libraries as recording: PyAV decodes the file on a background
thread and PortAudio (sounddevice) plays it. Seeking starts a fresh decoder
at the new position. The position is counted from the samples handed to the
sound card, minus its latency, so the transcript highlight stays in step with
what you hear.
"""

from __future__ import annotations

import queue
import threading

import numpy as np

# Decoded audio waiting to be played: roughly a second, enough to ride out
# a busy moment on the decoder thread without delaying seeks.
_BUFFERED_CHUNKS = 48


class PlayerError(Exception):
    pass


def file_duration(path: str) -> float:
    """Length in seconds, or 0 if the file doesn't say."""
    import av

    with av.open(path) as container:
        if container.duration:
            return container.duration / av.time_base
        stream = container.streams.audio[0]
        if stream.duration and stream.time_base:
            return float(stream.duration * stream.time_base)
    return 0.0


def decode(path: str, start: float, rate: int, chunks: queue.Queue, stop: threading.Event) -> None:
    """Puts mono float32 sample arrays from `start` seconds on into `chunks`,
    followed by None at the end of the file. Stops early when `stop` is set."""
    import av

    try:
        with av.open(path) as container:
            stream = container.streams.audio[0]
            if start > 0 and stream.time_base:
                # Lands on the last packet at or before `start`; the rest is trimmed below.
                container.seek(int(start / stream.time_base), stream=stream)
            resampler = av.AudioResampler(format="flt", layout="mono", rate=rate)
            skip: int | None = None

            def emit(frames) -> bool:
                nonlocal skip
                for out in frames:
                    samples = out.to_ndarray().reshape(-1).astype(np.float32, copy=False)
                    if skip:
                        dropped = min(skip, len(samples))
                        samples, skip = samples[dropped:], skip - dropped
                    if len(samples) and not _put(chunks, samples, stop):
                        return False
                return True

            for frame in container.decode(stream):
                if stop.is_set():
                    return
                if skip is None:
                    first = frame.time if frame.time is not None else start
                    skip = max(0, int(round((start - first) * rate)))
                if not emit(resampler.resample(frame)):
                    return
            if not emit(resampler.resample(None)):
                return
    except Exception:
        pass  # a damaged or cut-off file plays up to the damage
    _put(chunks, None, stop)


def _put(chunks: queue.Queue, item, stop: threading.Event) -> bool:
    while not stop.is_set():
        try:
            chunks.put(item, timeout=0.1)
            return True
        except queue.Full:
            pass
    return False


class AudioPlayer:
    def __init__(self) -> None:
        self.path: str | None = None
        self.duration = 0.0
        self.playing = False
        self._stream = None
        self._rate = 48_000
        self._base = 0.0  # where the current decoder started, in seconds
        self._played = 0  # samples handed to the sound card since then
        self._pending = np.zeros(0, dtype=np.float32)
        self._chunks: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._ended = False

    @property
    def position(self) -> float:
        """What is being heard right now, in seconds."""
        if self._ended and not len(self._pending):
            return self.duration
        played = self._played / self._rate
        if self.playing and self._stream is not None:
            # The last samples handed over are still on their way to the speakers.
            played = max(0.0, played - self._stream.latency)
        return min(self.duration, self._base + played)

    @property
    def ended(self) -> bool:
        """The whole file has been played."""
        return self._ended and not len(self._pending)

    def open(self, path: str, duration_hint: float = 0.0) -> None:
        """Gets ready to play `path`. The sound card isn't touched until play()."""
        self.close()
        try:
            duration = file_duration(path)
        except Exception as error:
            raise PlayerError(f"The audio file couldn't be opened: {error}") from error
        self.path = path
        # A recording cut short by a crash may not know its own length.
        self.duration = max(duration, duration_hint)
        self._restart(0.0)

    def close(self) -> None:
        self.pause()
        self._stop.set()
        if self._stream is not None:
            try:
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        self.path = None
        self.duration = 0.0
        self._base = 0.0
        self._played = 0
        self._ended = False

    def play(self) -> None:
        if self.path is None or self.playing:
            return
        if self.ended:
            self._restart(0.0)
        if self._stream is None:
            self._stream = self._open_stream()
        self.playing = True
        try:
            self._stream.start()
        except Exception as error:
            self.playing = False
            raise PlayerError(f"The audio couldn't be played: {error}") from error

    def pause(self) -> None:
        if not self.playing:
            return
        # Audio queued in the sound card is thrown away, so pick up again
        # exactly where it was heard to stop.
        heard = self.position
        self.playing = False
        try:
            self._stream.abort()
        except Exception:
            pass
        self._restart(heard)

    def seek(self, seconds: float) -> None:
        if self.path is None:
            return
        playing = self.playing
        if playing:
            self.playing = False
            try:
                self._stream.abort()
            except Exception:
                pass
        self._restart(max(0.0, min(seconds, self.duration)))
        if playing:
            self.play()

    def _open_stream(self):
        try:
            import sounddevice as sd
        except OSError as error:  # PortAudio missing
            raise PlayerError("The audio library PortAudio couldn't be loaded, so playback isn't possible. "
                              "On Linux, install the package “libportaudio2”.") from error
        try:
            device = sd.query_devices(kind="output")
            channels = min(2, int(device["max_output_channels"]))
            rate = int(device["default_samplerate"]) or 48_000
            if channels < 1:
                raise RuntimeError("the output device has no channels")
        except Exception as error:
            raise PlayerError("No speakers or headphones were found. Check your sound settings.") from error
        if rate != self._rate:
            self._rate = rate
            self._restart(self._base)
        try:
            return sd.OutputStream(samplerate=rate, channels=channels, dtype="float32",
                                   callback=self._on_audio)
        except Exception as error:
            raise PlayerError(f"The audio couldn't be played: {error}") from error

    def _restart(self, seconds: float) -> None:
        """Starts decoding from `seconds`. Only call while the stream is stopped."""
        self._stop.set()
        self._stop = threading.Event()
        self._chunks = queue.Queue(maxsize=_BUFFERED_CHUNKS)
        self._base = seconds
        self._played = 0
        self._pending = np.zeros(0, dtype=np.float32)
        self._ended = False
        if self.path is not None:
            threading.Thread(target=decode, args=(self.path, seconds, self._rate, self._chunks, self._stop),
                             daemon=True).start()

    # Runs on PortAudio's audio thread: keep it short.
    def _on_audio(self, outdata, frames, time, status) -> None:  # noqa: ARG002
        filled = 0
        while filled < frames:
            if not len(self._pending):
                if self._ended:
                    break
                try:
                    chunk = self._chunks.get_nowait()
                except queue.Empty:
                    break  # the decoder is behind; play silence for now
                if chunk is None:
                    self._ended = True
                    break
                self._pending = chunk
            count = min(frames - filled, len(self._pending))
            outdata[filled:filled + count] = self._pending[:count, None]
            self._pending = self._pending[count:]
            filled += count
        outdata[filled:] = 0
        self._played += filled
