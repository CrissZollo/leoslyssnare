import os
import sys


def _use_bundled_portaudio() -> None:
    """On Linux, sounddevice only looks for PortAudio among the system
    libraries. Point it at the copy bundled inside the AppImage instead, so
    users don't need to install anything."""
    if not (getattr(sys, "frozen", False) and sys.platform.startswith("linux")):
        return
    bundled = os.path.join(sys._MEIPASS, "libportaudio.so.2")
    if not os.path.exists(bundled):
        return
    import ctypes.util

    find_library = ctypes.util.find_library
    ctypes.util.find_library = lambda name: bundled if name == "portaudio" else find_library(name)


def _self_test() -> int:
    """Loads every native library the app needs and round-trips a short
    recording. Used by the build scripts to check a packaged app."""
    import queue
    import tempfile

    import ctranslate2
    import numpy as np
    import sherpa_onnx
    from faster_whisper.audio import decode_audio
    from faster_whisper.vad import get_vad_model

    from leoslyssnare.recorder import AudioRecorder

    get_vad_model()
    print("ctranslate2", ctranslate2.__version__, "· sherpa-onnx", sherpa_onnx.__version__)

    import sounddevice

    print("PortAudio:", sounddevice.get_portaudio_version()[1])

    from leoslyssnare.recorder import platform_format

    fmt = platform_format()
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "test" + fmt.extension)
        chunks = queue.Queue()
        chunks.put(np.zeros(48_000, dtype=np.float32))
        chunks.put(None)
        recorder = AudioRecorder(fmt)
        recorder._write(path, 48_000, chunks)
        if recorder._writer_error:
            raise recorder._writer_error
        print(f"{fmt.codec} {fmt.extension} round trip:", round(len(decode_audio(path)) / 16_000, 2), "s")

    from PySide6.QtWidgets import QApplication

    from leoslyssnare.app import MainWindow

    app = QApplication(sys.argv[:1])
    MainWindow()
    app.processEvents()
    print("Self-test passed.")
    return 0


def run() -> int:
    # The first transcription makes tqdm (inside faster-whisper) create a
    # multiprocessing lock, which starts multiprocessing's resource tracker by
    # running sys.executable again. In a packaged app that's the app itself,
    # so without this a second window opened. With it, that copy runs the
    # tracker and nothing else.
    import multiprocessing

    multiprocessing.freeze_support()
    _use_bundled_portaudio()
    if "--self-test" in sys.argv:
        # A windowed build has no console, so the result also goes to a file
        # when LEOSLYSSNARE_SELFTEST_LOG is set, and errors never open a dialog.
        log = os.environ.get("LEOSLYSSNARE_SELFTEST_LOG")
        if log:
            sys.stdout = sys.stderr = open(log, "w", encoding="utf-8")
        try:
            return _self_test()
        except BaseException:
            import traceback

            traceback.print_exc()
            return 1
        finally:
            if log:
                sys.stdout.flush()
    from leoslyssnare.app import main

    return main()


if __name__ == "__main__":
    sys.exit(run())
