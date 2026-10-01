"""Checks the parts of the pipeline that don't need a microphone or a
Whisper model: encoding a recording and reading it back."""

import queue

import numpy as np

from leoslyssnare.recorder import AudioRecorder


def test_recording_encodes_to_m4a_and_decodes(tmp_path):
    from faster_whisper.audio import decode_audio

    rate = 48_000
    path = str(tmp_path / "test.m4a")
    chunks: queue.Queue = queue.Queue()
    t = np.arange(rate * 2) / rate
    tone = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    for block in np.array_split(tone, 200):
        chunks.put(block)
    chunks.put(None)

    recorder = AudioRecorder()
    recorder._write(path, rate, chunks)
    assert recorder._writer_error is None

    audio = decode_audio(path, sampling_rate=16_000)
    assert abs(len(audio) / 16_000 - 2.0) < 0.1
    assert 0.15 < float(np.sqrt(np.mean(audio ** 2))) < 0.3
