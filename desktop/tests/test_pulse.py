"""Checks choosing a microphone and mixing in an app's sound without a sound
server: pactl's output is faked, and the app's sound is fed in by hand."""

import numpy as np

from leoslyssnare import pulse, sources
from leoslyssnare.recorder import RECORDING, AudioRecorder

SOURCES = """Source #63
\tState: RUNNING
\tName: alsa_output.usb-Jabra.analog-stereo.monitor
\tDescription: Monitor of Jabra Engage 65 SE Analog Stereo
\tMonitor of Sink: alsa_output.usb-Jabra.analog-stereo
\tProperties:
\t\tdevice.class = "monitor"
Source #64
\tState: RUNNING
\tName: alsa_input.usb-Jabra.mono-fallback
\tDescription: Jabra Engage 65 SE Mono
\tMonitor of Sink: n/a
\tProperties:
\t\tdevice.class = "sound"
Source #92
\tState: RUNNING
\tName: easyeffects_source
\tDescription: Easy Effects Source
\tMonitor of Sink: n/a
\tProperties:
\t\tmedia.class = "Audio/Source/Virtual"
"""


def sink_input(index: int, name: str, binary: str, pid: int = 5986) -> str:
    return (f"Sink Input #{index}\n\tDriver: PipeWire\n\tCorked: no\n\tProperties:\n"
            f'\t\tapplication.name = "{name}"\n\t\tapplication.process.id = "{pid}"\n'
            f'\t\tapplication.process.binary = "{binary}"\n\t\tmedia.name = "Playback"\n')


def fake_pactl(monkeypatch, sink_inputs: str) -> None:
    outputs = {("info",): "Server Name: PulseAudio (on PipeWire 1.0.5)\nDefault Source: alsa_input.usb-Jabra.mono-fallback\n",
               ("list", "sources"): SOURCES,
               ("list", "sink-inputs"): sink_inputs}
    monkeypatch.setattr(pulse, "_pactl", lambda *args: outputs.get(args))


def test_lists_microphones_but_not_monitors(monkeypatch):
    fake_pactl(monkeypatch, "")
    assert [mic.description for mic in pulse.microphones()] == ["Jabra Engage 65 SE Mono", "Easy Effects Source"]
    assert pulse.default_microphone() == "alsa_input.usb-Jabra.mono-fallback"


def test_lists_each_app_once_and_not_this_one(monkeypatch):
    import os

    fake_pactl(monkeypatch, sink_input(1, "Firefox", "firefox") + sink_input(2, "Firefox", "firefox")
               + sink_input(3, "Chromium", "teams-for-linux")
               + sink_input(4, "Leos Lyssnare", "python3", pid=os.getpid()))
    assert pulse.applications() == [sources.Application("teams-for-linux", "Chromium (teams-for-linux)"),
                                    sources.Application("firefox", "Firefox")]


def test_app_audio_follows_the_apps_streams(monkeypatch):
    started = []

    class FakeCapture:
        def __init__(self, args, on_audio, rate):
            self.args, self.on_audio, self.running = args, on_audio, True

        def start(self):
            started.append(self.args)

        def stop(self):
            self.running = False

    monkeypatch.setattr(pulse, "Capture", FakeCapture)
    monkeypatch.setattr(sources, "_backend", lambda: pulse)
    fake_pactl(monkeypatch, sink_input(7, "Chromium", "teams-for-linux") + sink_input(8, "Firefox", "firefox"))
    app = sources.app_audio("teams-for-linux", pulse.RATE)
    app._find_streams()
    assert started == [["--monitor-stream=7"]]
    # The call opens a second stream later on.
    fake_pactl(monkeypatch, sink_input(7, "Chromium", "teams-for-linux") + sink_input(9, "Chromium", "teams-for-linux"))
    app._find_streams()
    assert started[-1] == ["--monitor-stream=9"]
    assert app.stream_count == 2

    for capture, _ in app._streams.values():
        capture.on_audio(np.full(pulse.RATE // 10, 0.25, dtype=np.float32))
    assert np.allclose(app.take(480), 0.5)  # both streams, mixed
    app.stop()
    assert app.stream_count == 0


def test_all_sound_is_the_default_output(monkeypatch):
    assert pulse.find_streams(sources.ALL_SOUND) == {-1: ["--device=@DEFAULT_MONITOR@"]}


def test_jitter_buffer_waits_pads_and_skips_ahead():
    buffer = sources.JitterBuffer(48_000)
    prebuffer = buffer.prebuffer
    buffer.push(np.ones(prebuffer // 2, dtype=np.float32))
    assert not buffer.take(100).any()  # still collecting
    buffer.push(np.ones(prebuffer // 2, dtype=np.float32))
    assert buffer.take(100).all()
    # Running dry pads with silence, then waits until it has a reserve again.
    out = buffer.take(prebuffer)
    assert out[:prebuffer - 100].all() and not out[prebuffer - 100:].any()
    buffer.push(np.ones(10, dtype=np.float32))
    assert not buffer.take(10).any()

    # Far too much: only the newest PREBUFFER is kept.
    buffer = sources.JitterBuffer(48_000)
    buffer.push(np.zeros(buffer.limit, dtype=np.float32))
    buffer.push(np.ones(1000, dtype=np.float32))
    assert len(buffer) == prebuffer
    assert buffer.take(prebuffer)[-1000:].all()


class FixedApp:
    def __init__(self, value: float):
        self.value, self.taken = value, 0

    def take(self, count):
        self.taken += count
        return np.full(count, self.value, dtype=np.float32)


def test_recorder_mixes_the_app_in_and_leaves_it_out_while_paused():
    recorder = AudioRecorder()
    recorder.state = RECORDING
    recorder._app = FixedApp(0.25)
    recorder._on_audio(np.full(480, 0.5, dtype=np.float32))
    assert np.allclose(recorder._queue.get_nowait(), 0.75)

    recorder._app = FixedApp(0.75)
    recorder._on_audio(np.full(480, 0.5, dtype=np.float32))
    assert np.allclose(recorder._queue.get_nowait(), 1.0)  # clipped, not wrapped

    recorder.pause()
    recorder._on_audio(np.full(480, 0.5, dtype=np.float32))
    assert recorder._queue.empty()
    assert recorder._app.taken == 960  # the paused part was thrown away, not kept for later
    assert recorder.elapsed == 480 / recorder._sample_rate * 2
