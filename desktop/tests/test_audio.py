"""Checks the parts of the pipeline that don't need a microphone or a
Whisper model: encoding a recording and reading it back."""

import queue

import numpy as np
import pytest

from leoslyssnare.recorder import MP3, OGG_OPUS, AudioRecorder


def _encode(path, rate, fmt, seconds=2.0):
    chunks: queue.Queue = queue.Queue()
    t = np.arange(int(rate * seconds)) / rate
    tone = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    for block in np.array_split(tone, 200):
        chunks.put(block)
    chunks.put(None)
    recorder = AudioRecorder(fmt)
    recorder._write(path, rate, chunks)
    assert recorder._writer_error is None


# 44.1 kHz into Opus checks that the encoder resamples to 48 kHz.
@pytest.mark.parametrize("fmt", [MP3, OGG_OPUS], ids=["mp3", "ogg-opus"])
@pytest.mark.parametrize("rate", [44_100, 48_000])
def test_recording_encodes_and_decodes(tmp_path, fmt, rate):
    import av
    from faster_whisper.audio import decode_audio

    path = str(tmp_path / f"test{fmt.extension}")
    _encode(path, rate, fmt)

    with av.open(path) as container:
        assert container.streams.audio[0].codec_context.name in ("mp3", "mp3float", "opus", "libopus")

    audio = decode_audio(path, sampling_rate=16_000)
    assert abs(len(audio) / 16_000 - 2.0) < 0.1
    assert 0.15 < float(np.sqrt(np.mean(audio ** 2))) < 0.3


@pytest.mark.parametrize("fmt", [MP3, OGG_OPUS], ids=["mp3", "ogg-opus"])
def test_truncated_recording_is_still_readable(tmp_path, fmt):
    """What's left after a crash mid-recording can still be transcribed."""
    from faster_whisper.audio import decode_audio

    path = tmp_path / f"test{fmt.extension}"
    _encode(str(path), 48_000, fmt, seconds=10)
    data = path.read_bytes()
    path.write_bytes(data[: len(data) // 2])
    audio = decode_audio(str(path), sampling_rate=16_000)
    assert len(audio) / 16_000 > 3
