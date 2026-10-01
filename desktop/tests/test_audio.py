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


def test_unreadable_files_get_a_helpful_message(tmp_path):
    """An empty or cut-off download explains itself instead of showing FFmpeg's error."""
    from faster_whisper.audio import decode_audio

    from leoslyssnare.engine import UnreadableAudio, read_audio

    empty = tmp_path / "Meeting.mp3"
    empty.touch()
    with pytest.raises(UnreadableAudio, match="still downloading"):
        read_audio(str(empty), decode_audio)

    good = tmp_path / "good.mp3"
    _encode(str(good), 48_000, MP3)
    broken = tmp_path / "broken.mp3"
    broken.write_bytes(b"ID3\x04\x00\x00\x00\x07\x52\x12" + good.read_bytes()[:200])
    with pytest.raises(UnreadableAudio, match="couldn't be read as audio"):
        read_audio(str(broken), decode_audio)

    with pytest.raises(UnreadableAudio, match="couldn't be found"):
        read_audio(str(tmp_path / "gone.mp3"), decode_audio)
    assert len(read_audio(str(good), decode_audio)) > 16_000


def test_find_audio_by_name(tmp_path, monkeypatch):
    from leoslyssnare import paths

    monkeypatch.setattr(paths, "documents", lambda: str(tmp_path / "docs"))
    monkeypatch.setattr(paths, "recordings", lambda: str(tmp_path / "rec"))
    monkeypatch.setattr(paths, "user_downloads_dir", lambda: str(tmp_path / "dl"))
    for folder in ("docs", "rec", "dl", "txt"):
        (tmp_path / folder).mkdir()
    transcript = str(tmp_path / "txt" / "Meeting.txt")
    missing = str(tmp_path / "gone" / "Meeting.ogg")
    assert paths.find_audio(missing, transcript) is None
    (tmp_path / "dl" / "Meeting.mp3").touch()
    assert paths.find_audio(missing, transcript) == str(tmp_path / "dl" / "Meeting.mp3")
    (tmp_path / "rec" / "Meeting.ogg").touch()  # the exact name wins
    assert paths.find_audio(missing, transcript) == str(tmp_path / "rec" / "Meeting.ogg")
