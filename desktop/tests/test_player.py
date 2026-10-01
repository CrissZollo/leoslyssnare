"""Playback decoding, without a sound card: the decoder starts at the right
place and the player reports positions and length."""

import queue
import threading

import numpy as np
import pytest

from leoslyssnare.player import AudioPlayer, decode
from leoslyssnare.recorder import MP3, OGG_OPUS

from test_audio import _encode


def _decoded_seconds(path, start, rate=48_000):
    chunks: queue.Queue = queue.Queue()
    decode(path, start, rate, chunks, threading.Event())
    samples = []
    while (chunk := chunks.get()) is not None:
        samples.append(chunk)
    audio = np.concatenate(samples)
    assert audio.dtype == np.float32 and audio.ndim == 1
    return len(audio) / rate


@pytest.mark.parametrize("fmt", [MP3, OGG_OPUS], ids=["mp3", "ogg-opus"])
def test_decode_from_start_and_middle(tmp_path, fmt):
    path = str(tmp_path / f"test{fmt.extension}")
    _encode(path, 48_000, fmt, seconds=4.0)
    assert abs(_decoded_seconds(path, 0.0) - 4.0) < 0.1
    assert abs(_decoded_seconds(path, 2.5) - 1.5) < 0.1
    assert abs(_decoded_seconds(path, 1.0, rate=44_100) - 3.0) < 0.1


def test_player_reports_length_and_seeks_without_a_sound_card(tmp_path):
    path = str(tmp_path / "test.ogg")
    _encode(path, 48_000, OGG_OPUS, seconds=3.0)
    player = AudioPlayer()
    player.open(path)
    assert abs(player.duration - 3.0) < 0.1
    assert player.position == 0.0
    player.seek(1.25)
    assert player.position == pytest.approx(1.25)
    player.seek(99)
    assert player.position == pytest.approx(player.duration)
    player.close()
    assert player.path is None


def test_callback_counts_played_samples(tmp_path):
    path = str(tmp_path / "test.ogg")
    _encode(path, 48_000, OGG_OPUS, seconds=1.0)
    player = AudioPlayer()
    player.open(path)
    out = np.ones((48_000 * 2, 2), dtype=np.float32)
    deadline = threading.Event()
    for _ in range(100):  # let the decoder thread fill the queue
        player._on_audio(out[:4_800], 4_800, None, None)
        if player.ended:
            break
        deadline.wait(0.01)
    assert player.ended
    assert player.position == player.duration
    player.close()
