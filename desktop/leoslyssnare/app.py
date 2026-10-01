"""The main window. Same layout and behaviour as the macOS app's ContentView."""

from __future__ import annotations

import bisect
import copy
import html
import os
import subprocess
import sys
import threading
import time

from PySide6.QtCore import (QEasingCurve, QEvent, QObject, QSettings, Qt, QTimer, QUrl, QVariantAnimation,
                            Signal)
from PySide6.QtGui import QColor, QDesktopServices, QGuiApplication, QIcon, QPixmap, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (QApplication, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QMainWindow, QMenu, QMessageBox, QScrollArea, QSizePolicy,
                               QTextBrowser, QTextEdit, QVBoxLayout, QWidget)

from . import engine, icons, keepawake, paths, theme
from .player import AudioPlayer, PlayerError
from .recorder import IDLE, PAUSED, RECORDING, AudioRecorder, RecorderError
from .theme import setup_application  # noqa: F401  (re-exported for the entry point)
from .transcript import NotATranscript, Transcript, timestamp
from .transcript import load as load_transcript
from .widgets import (Avatar, Button, Card, Combo, DropZone, ElidedLabel, Motif, ProgressBar, Rule,
                      Scrubber, ShareBar, StatusDot, Switch, Waveform, label)

AUDIO_EXTENSIONS = paths.AUDIO_EXTENSIONS

SIDEBAR_WIDTH = 372
SPEAKERS_WIDTH = 268


def resource_path(name: str) -> str:
    return theme.resource_path(name)


def system_env() -> dict[str, str]:
    """The environment for system programs the app starts. A PyInstaller build
    points LD_LIBRARY_PATH at its own Qt, and Qt programs such as Dolphin or
    kde-open fail to start when they load that instead of the system's."""
    env = dict(os.environ)
    if getattr(sys, "frozen", False):
        original = env.pop("LD_LIBRARY_PATH_ORIG", None)
        if original is None:
            env.pop("LD_LIBRARY_PATH", None)
        else:
            env["LD_LIBRARY_PATH"] = original
        for name in ("QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "QML2_IMPORT_PATH"):
            env.pop(name, None)
    return env


def open_folder(path: str) -> None:
    if sys.platform.startswith("linux"):
        try:
            subprocess.Popen(["xdg-open", path], env=system_env(), stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            return
        except OSError:
            pass  # no xdg-open: let Qt try
    QDesktopServices.openUrl(QUrl.fromLocalFile(path))


def show_in_file_manager(path: str) -> None:
    if sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    else:
        open_folder(os.path.dirname(path))


def language_name(code: str | None) -> str:
    return dict(engine.LANGUAGES).get(code or "", code or "")


def clear_layout(layout) -> None:
    while layout.count():
        widget = layout.takeAt(0).widget()
        if widget is not None:
            # Off screen right away; deleteLater only happens on the next pass of the event loop.
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()


def display_text(segment) -> str:
    """A segment's text as the transcript view shows it, with single spaces."""
    return " ".join(segment.text.split())


class Worker(QObject):
    """Runs engine work on a background thread and reports back through
    signals, which Qt delivers on the UI thread."""

    progress = Signal(float)
    status = Signal(str)
    finished = Signal(object)  # Transcript, or None
    failed = Signal(str)
    cancelled = Signal()

    def run(self, job) -> None:
        def target() -> None:
            try:
                self.finished.emit(job(self.progress.emit, self.status.emit))
            except engine.Cancelled:
                self.cancelled.emit()
            except Exception as error:  # shown to the user
                self.failed.emit(str(error) or type(error).__name__)

        threading.Thread(target=target, daemon=True).start()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        setup_application(QApplication.instance())
        self.setWindowTitle("Leos Lyssnare")
        self.setMinimumSize(1000, 640)
        self.resize(1180, 780)
        self.setAcceptDrops(True)

        self.settings = QSettings("LeosLyssnare", "LeosLyssnare")
        geometry = self.settings.value("windowGeometry")
        if geometry:
            self.restoreGeometry(geometry)
        self.engine = engine.Engine()
        self.recorder = AudioRecorder()
        self.transcript: Transcript | None = None
        self.busy = False
        self.cancel_flag: engine.CancelFlag | None = None
        self.speaker_edits: dict[int, QLineEdit] = {}
        self.speaker_avatars: dict[int, Avatar] = {}
        # The transcript as it was before the last merge, for Undo.
        self.before_merge: tuple[list, dict] | None = None

        self.player = AudioPlayer()
        # Where each segment's text sits in the transcript view, as character
        # positions, and the same for each timed word: (start, end, first, last).
        self.segment_spans: list[tuple[int, int]] = []
        self.word_spans: list[list[tuple[float, float, int, int]]] = []
        self._playhead_shown = False  # highlight only once the user has played or jumped
        self._highlighted: tuple | None = None
        self._follow = True  # keep the spoken text in the middle of the view
        self.player_error: str | None = None

        self.worker = Worker()
        self.worker.progress.connect(self._on_progress)
        self.worker.status.connect(self._on_status)
        self.worker.failed.connect(self._on_failed)
        self.worker.finished.connect(self._on_worker_finished)
        self._on_done = None
        self.worker.cancelled.connect(self._on_cancelled)

        central = QWidget()
        central.setObjectName("root")
        root = QVBoxLayout(central)
        root.setContentsMargins(24, 16, 24, 24)
        root.setSpacing(16)
        root.addWidget(self._build_header())

        columns = QHBoxLayout()
        columns.setSpacing(20)
        columns.addWidget(self._build_sidebar())
        right = QVBoxLayout()
        right.setSpacing(14)
        right.addWidget(self._build_status_bar())
        right.addWidget(self._build_transcript_section(), 1)
        columns.addLayout(right, 1)
        root.addLayout(columns, 1)
        self.setCentralWidget(central)

        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self._tick)
        self.play_timer = QTimer(self)
        self.play_timer.setInterval(40)
        self.play_timer.timeout.connect(self._on_play_tick)

        theme.bus.changed.connect(self._on_theme_changed)
        self._on_theme_changed()
        self._refresh_model_state()
        self._refresh_recorder()
        self._refresh_status(False)
        self._show_transcript()

    def _on_theme_changed(self) -> None:
        t = theme.current()
        self.tile_icon.setPixmap(icons.pixmap("waveform", t.brand, 22))
        self.pill_icon.setPixmap(icons.pixmap("shield-check", t.ok, 16))
        if self.transcript is not None:
            self._render_text()

    # MARK: - Header

    def _build_header(self) -> QWidget:
        header = QWidget()
        row = QHBoxLayout(header)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)

        logo = QLabel()
        mark = QPixmap(resource_path("icon.png")).scaled(64, 64, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        mark.setDevicePixelRatio(2)
        logo.setPixmap(mark)
        logo.setFixedSize(32, 32)
        row.addWidget(logo)
        row.addWidget(label("Leos Lyssnare", "appTitle"))
        row.addStretch(1)

        self.offline_pill = QFrame()
        self.offline_pill.setObjectName("pillOk")
        self.offline_pill.setFixedHeight(30)
        self.offline_pill.setToolTip("The speech models are on this computer. Audio and text never leave it.")
        pill = QHBoxLayout(self.offline_pill)
        pill.setContentsMargins(11, 0, 14, 0)
        pill.setSpacing(7)
        self.pill_icon = QLabel()
        pill.addWidget(self.pill_icon)
        pill.addWidget(QLabel("Available offline"))
        row.addWidget(self.offline_pill)

        self.download_button = Button("Download models", "download", "primary",
                                      tooltip="One-time download that needs internet. After that, everything runs offline.")
        self.download_button.clicked.connect(self._download_models)
        row.addWidget(self.download_button)

        open_transcript = Button("Open transcript…", "file-text", "ghost",
                                 tooltip="Open a saved transcript, with its recording if it can be found")
        open_transcript.clicked.connect(self._choose_transcript)
        row.addWidget(open_transcript)

        recordings = Button("Recordings", "folder", "ghost", tooltip="Open the recordings folder")
        recordings.clicked.connect(lambda: open_folder(paths.recordings()))
        row.addWidget(recordings)
        return header

    # MARK: - Sidebar

    def _build_sidebar(self) -> QScrollArea:
        content = QWidget()
        column = QVBoxLayout(content)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(14)
        column.addWidget(self._build_record_card())
        column.addWidget(self._build_file_card())
        column.addWidget(self._build_model_section())
        column.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFixedWidth(SIDEBAR_WIDTH)
        scroll.setWidget(content)
        return scroll

    # MARK: - Model

    def _build_model_section(self) -> Card:
        card = Card(spacing=6)
        card.body.addWidget(label("Speech recognition", "cardTitle"))
        card.body.addSpacing(4)

        card.body.addWidget(label("Model", "muted"))
        self.model_combo = Combo()
        for option in engine.MODELS:
            self.model_combo.addItem(f"{option.name} ({option.size})", option.id)
        self._select(self.model_combo, self.settings.value("selectedModel", engine.MODELS[0].id))
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        card.body.addWidget(self.model_combo)

        card.body.addSpacing(6)
        card.body.addWidget(label("Language", "muted"))
        self.language_combo = Combo()
        for code, name in engine.LANGUAGES:
            self.language_combo.addItem(name, code)
        self._select(self.language_combo, self.settings.value("language", "sv"))
        self.language_combo.currentIndexChanged.connect(
            lambda: self.settings.setValue("language", self.language_combo.currentData()))
        card.body.addWidget(self.language_combo)

        card.body.addSpacing(10)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.speakers_check = Switch("Identify speakers")
        self.speakers_check.setChecked(self.settings.value("identifySpeakers", True, type=bool))
        self.speakers_check.toggled.connect(self._on_identify_toggled)
        row.addWidget(self.speakers_check)
        row.addStretch(1)
        self.count_combo = Combo()
        self.count_combo.setFixedWidth(128)
        self.count_combo.setToolTip("Number of speakers")
        self.count_combo.addItem("Automatic", 0)
        for count in range(2, 13):
            self.count_combo.addItem(f"{count} people", count)
        self._select(self.count_combo, self.settings.value("speakerCount", 0, type=int))
        self.count_combo.currentIndexChanged.connect(
            lambda: self.settings.setValue("speakerCount", self.count_combo.currentData()))
        row.addWidget(self.count_combo)
        card.body.addLayout(row)

        card.body.addWidget(label("Know how many people took part? Choose the number.", "caption"))

        self.model_controls = [self.model_combo, self.language_combo, self.download_button,
                               self.speakers_check, self.count_combo]
        return card

    @staticmethod
    def _select(combo: Combo, value) -> None:
        for i in range(combo.count()):
            if combo.itemData(i) is not None and str(combo.itemData(i)) == str(value):
                combo.setCurrentIndex(i)
                return

    def _on_model_changed(self) -> None:
        self.settings.setValue("selectedModel", self.model_combo.currentData())
        self.engine.unload_whisper()
        self._refresh_model_state()

    def _on_identify_toggled(self, on: bool) -> None:
        self.settings.setValue("identifySpeakers", on)
        self._refresh_model_state()

    def _ready_offline(self) -> bool:
        return engine.whisper_ready(self.model_combo.currentData()) and (
            not self.speakers_check.isChecked() or engine.speaker_models_ready())

    def _refresh_model_state(self) -> None:
        ready = self._ready_offline()
        self.offline_pill.setVisible(ready)
        self.download_button.setVisible(not ready)
        for control in self.model_controls:
            control.setEnabled(not self.busy)
        self.count_combo.setEnabled(not self.busy and self.speakers_check.isChecked())

    def _download_models(self) -> None:
        if self.busy:
            return
        model_id = self.model_combo.currentData()
        with_speakers = self.speakers_check.isChecked()

        def job(progress, status):
            self.engine.download(model_id, with_speakers, progress, status)

        self._begin_work(cancellable=False)
        self._on_done = self._on_download_finished
        self.worker.run(job)

    def _on_download_finished(self, _result) -> None:
        self._end_work()

    # MARK: - Recording

    def _build_record_card(self) -> Card:
        card = Card(margins=(20, 18, 20, 20), spacing=12)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(label("Record a meeting", "cardTitle"))
        row.addStretch(1)
        self.dot = StatusDot()
        row.addWidget(self.dot)
        self.record_status = label("", "muted")
        row.addWidget(self.record_status)
        card.body.addLayout(row)

        self.elapsed_label = label(timestamp(0), "timer")
        self.elapsed_label.setFont(theme.tabular(self.elapsed_label.font()))
        card.body.addWidget(self.elapsed_label)

        self.waveform = Waveform()
        card.body.addWidget(self.waveform)

        row = QHBoxLayout()
        row.setSpacing(10)
        self.start_button = Button("Start recording", "record", "record", "lg")
        self.start_button.clicked.connect(self._start_recording)
        self.pause_button = Button("Pause", "pause", "secondary", "lg")
        self.pause_button.clicked.connect(self._pause_or_resume)
        self.stop_button = Button("Stop", "stop", "dark", "lg")
        self.stop_button.clicked.connect(self._stop_recording)
        for button in (self.start_button, self.pause_button, self.stop_button):
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            row.addWidget(button)
        card.body.addLayout(row)
        return card

    def _start_recording(self) -> None:
        # Otherwise the microphone would pick up the playback.
        self.player.pause()
        self._refresh_player()
        try:
            self.recorder.start()
        except RecorderError as error:
            self._show_error(str(error))
        except Exception as error:
            self._show_error(f"Couldn't start recording: {error}")
        self._refresh_recorder()
        if self.recorder.state != IDLE:
            self.timer.start()

    def _pause_or_resume(self) -> None:
        if self.recorder.state == RECORDING:
            self.recorder.pause()
        else:
            self.recorder.resume()
        self._refresh_recorder()

    def _stop_recording(self) -> None:
        self.timer.stop()
        try:
            path = self.recorder.stop()
        except RecorderError as error:
            self._refresh_recorder()
            self._show_error(str(error))
            return
        self._refresh_recorder()
        if not path:
            return
        box = QMessageBox(QMessageBox.NoIcon, "Transcribe the recording?",
                          f"The recording was saved as “{os.path.basename(path)}”. Do you want to transcribe it now?",
                          QMessageBox.NoButton, self)
        yes = box.addButton("Transcribe", QMessageBox.AcceptRole)
        box.addButton("Not now", QMessageBox.RejectRole)
        box.setDefaultButton(yes)
        box.exec()
        if box.clickedButton() is yes:
            self.transcribe(path)

    def _tick(self) -> None:
        self.elapsed_label.setText(timestamp(self.recorder.elapsed))
        if self.recorder.state == RECORDING:
            self.waveform.push(self.recorder.level)
            self.dot.set_state("record", theme.pulse(time.monotonic() / 1.4 % 1.0))

    def _refresh_recorder(self) -> None:
        state = self.recorder.state
        self.record_status.setText({IDLE: "Ready", RECORDING: "Recording", PAUSED: "Paused"}[state])
        self.record_status.setToolTip("Paused time is left out of the recording." if state == PAUSED else "")
        self.dot.set_state({IDLE: "faint", RECORDING: "record", PAUSED: "warn"}[state])
        self.waveform.set_mode(state)
        self.start_button.setVisible(state == IDLE)
        self.pause_button.setVisible(state != IDLE)
        self.stop_button.setVisible(state != IDLE)
        if hasattr(self, "play_button"):
            self._refresh_player()
        if state == PAUSED:
            self.pause_button.setText("Resume")
            self.pause_button.set_icon_name("record")
            self.pause_button.set_variant("record")
        else:
            self.pause_button.setText("Pause")
            self.pause_button.set_icon_name("pause")
            self.pause_button.set_variant("secondary")
        self._tick()

    # MARK: - Existing file

    def _build_file_card(self) -> DropZone:
        self.drop_area = DropZone()
        column = QVBoxLayout(self.drop_area)
        column.setContentsMargins(16, 14, 16, 16)
        column.setSpacing(12)

        row = QHBoxLayout()
        row.setSpacing(12)
        tile = QFrame()
        tile.setObjectName("tile")
        tile.setFixedSize(44, 44)
        inner = QVBoxLayout(tile)
        inner.setContentsMargins(0, 0, 0, 0)
        self.tile_icon = QLabel()
        self.tile_icon.setAlignment(Qt.AlignCenter)
        inner.addWidget(self.tile_icon)
        row.addWidget(tile)
        text = QVBoxLayout()
        text.setSpacing(1)
        text.addWidget(label("Transcribe an existing file", "rowTitle"))
        text.addWidget(label("Drop it here (.m4a, .mp3, .wav…)", "caption"))
        row.addLayout(text, 1)
        column.addLayout(row)

        self.choose_button = Button("Choose file…", "folder-search")
        self.choose_button.clicked.connect(self._choose_file)
        column.addWidget(self.choose_button)
        return self.drop_area

    def _choose_file(self) -> None:
        patterns = " ".join(f"*{ext}" for ext in AUDIO_EXTENSIONS)
        path, _ = QFileDialog.getOpenFileName(self, "Choose an audio file", "",
                                              f"Audio files ({patterns});;Transcripts (*.txt);;All files (*)")
        if path:
            self.open_file(path)

    def open_file(self, path: str) -> None:
        """A .txt is a saved transcript to show; anything else is audio to transcribe."""
        if path.lower().endswith(".txt"):
            self.open_transcript(path)
        else:
            self.transcribe(path)

    def _dropped_file(self, event) -> str | None:
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        files = [u.toLocalFile() for u in urls if u.isLocalFile()]
        return files[0] if files else None

    def dragEnterEvent(self, event) -> None:
        if self._dropped_file(event):
            event.acceptProposedAction()
            self.drop_area.set_targeted(True)

    def dragLeaveEvent(self, event) -> None:
        self.drop_area.set_targeted(False)

    def dropEvent(self, event) -> None:
        self.drop_area.set_targeted(False)
        path = self._dropped_file(event)
        if path:
            event.acceptProposedAction()
            self.open_file(path)

    # MARK: - Transcription

    def transcribe(self, path: str) -> None:
        if self.busy:
            self._show_error("A transcription is already running. Wait for it to finish or stop it first.")
            return
        model_id = self.model_combo.currentData()
        language = self.language_combo.currentData()
        speaker_count = self.count_combo.currentData() if self.speakers_check.isChecked() else None
        cancel = engine.CancelFlag()

        def job(progress, status):
            return self.engine.transcribe(path, model_id, language, speaker_count, progress, status, cancel)

        self.cancel_flag = cancel
        self._begin_work(cancellable=True)
        keepawake.acquire()
        self._on_done = self._on_transcribed
        self.worker.run(job)

    def _on_transcribed(self, transcript: Transcript) -> None:
        keepawake.release()
        self._end_work()
        # Save automatically to Documents/LeosLyssnare/Transcripts.
        save_path = paths.transcript_path_for(transcript.source_path)
        try:
            self._save(transcript, save_path)
            transcript.saved_path = save_path
        except OSError as error:
            self._show_error(f"Transcription finished but couldn't be saved: {error}")
        self.transcript = transcript
        self._show_transcript()

    # MARK: - Saved transcripts

    def _choose_transcript(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open a transcript", paths.transcripts(),
                                              "Transcripts (*.txt);;All files (*)")
        if path:
            self.open_transcript(path)

    def open_transcript(self, path: str) -> None:
        try:
            transcript = load_transcript(path, paths.transcript_data(path))
        except (OSError, UnicodeDecodeError) as error:
            self._show_error(f"The transcript couldn't be opened: {error}")
            return
        except NotATranscript as error:
            self._show_error(str(error))
            return
        # The recording usually has the same name as in the transcript's first line.
        audio = paths.find_audio(transcript.source_path, path)
        if audio:
            transcript.source_path = audio
        self.transcript = transcript
        self._show_transcript()

    @staticmethod
    def _save(transcript: Transcript, path: str) -> None:
        transcript.save(path, paths.transcript_data(path))

    def _save_quietly(self) -> None:
        """Keeps the saved file in step with renames and merges."""
        if self.transcript and self.transcript.saved_path:
            try:
                self._save(self.transcript, self.transcript.saved_path)
            except OSError:
                pass

    def _choose_audio(self) -> None:
        """Connects a transcript to its recording by hand, when it wasn't found."""
        if self.transcript is None:
            return
        patterns = " ".join(f"*{ext}" for ext in AUDIO_EXTENSIONS)
        start = os.path.dirname(self.transcript.saved_path or "") or paths.recordings()
        path, _ = QFileDialog.getOpenFileName(self, f"Find “{self.transcript.source_name}”", start,
                                              f"Audio files ({patterns});;All files (*)")
        if not path:
            return
        self.transcript.source_path = path
        self._save_quietly()
        self.title_label.setText(self.transcript.source_name)
        self.title_label.setToolTip(path)
        self._load_audio(self.transcript)

    # MARK: - Status

    def _build_status_bar(self) -> QFrame:
        self.status_widget = QFrame()
        self.status_widget.setObjectName("statusCard")
        column = QVBoxLayout(self.status_widget)
        column.setContentsMargins(18, 12, 14, 14)
        column.setSpacing(10)

        row = QHBoxLayout()
        row.setSpacing(12)
        self.status_label = label("", "rowTitle", wrap=True)
        self.status_label.setMinimumWidth(0)
        row.addWidget(self.status_label, 1)
        self.percent_label = label("", "rowTitle")
        self.percent_label.setFont(theme.tabular(self.percent_label.font()))
        row.addWidget(self.percent_label)
        self.cancel_button = Button("Stop", "stop", "secondary")
        self.cancel_button.clicked.connect(self._cancel)
        row.addWidget(self.cancel_button)
        column.addLayout(row)

        self.progress_bar = ProgressBar()
        column.addWidget(self.progress_bar)
        return self.status_widget

    def _begin_work(self, cancellable: bool) -> None:
        self.busy = True
        self.status_label.setText("")
        self._on_progress(0.0)
        self._refresh_status(cancellable)
        self._refresh_model_state()
        self.choose_button.setEnabled(False)

    def _end_work(self, keep_status: bool = False) -> None:
        self.busy = False
        self.cancel_flag = None
        if not keep_status:
            self.status_label.setText("")
        self._refresh_status(False)
        self._refresh_model_state()
        self.choose_button.setEnabled(True)

    def _on_worker_finished(self, result) -> None:
        done, self._on_done = self._on_done, None
        if done is not None:
            done(result)

    def _refresh_status(self, cancellable: bool) -> None:
        self.status_widget.setVisible(self.busy or bool(self.status_label.text()))
        self.progress_bar.setVisible(self.busy)
        self.percent_label.setVisible(self.busy and self.progress_bar._value is not None)
        self.cancel_button.setVisible(self.busy and cancellable)

    def _on_progress(self, fraction: float) -> None:
        if fraction > 0:
            self.progress_bar.set_value(fraction)
            self.percent_label.setText(f"{int(fraction * 100)} %")
            self.percent_label.setVisible(self.busy)
        else:
            self.progress_bar.set_value(None)  # endless
            self.percent_label.setVisible(False)

    def _on_status(self, text: str) -> None:
        self.status_label.setText(text)

    def _on_failed(self, message: str) -> None:
        was_transcribing = self.cancel_flag is not None
        if was_transcribing:
            keepawake.release()
        self._on_done = None
        self._end_work()
        prefix = "Transcription failed" if was_transcribing else "Download failed"
        self._show_error(f"{prefix}: {message}")

    def _on_cancelled(self) -> None:
        keepawake.release()
        self._on_done = None
        self.status_label.setText("Transcription stopped.")
        self._end_work(keep_status=True)

    def _cancel(self) -> None:
        if self.cancel_flag is not None:
            self.cancel_flag.set()
            self.status_label.setText("Stopping…")

    # MARK: - Transcript

    def _build_transcript_section(self) -> Card:
        card = Card(margins=(24, 20, 24, 22), spacing=14)

        # Shown before the first transcript.
        self.empty_view = QWidget()
        empty = QVBoxLayout(self.empty_view)
        empty.setSpacing(10)
        empty.addStretch(2)
        empty.addWidget(Motif(), 0, Qt.AlignCenter)
        empty.addSpacing(10)
        empty.addWidget(label("Your transcript will appear here", "emptyTitle"), 0, Qt.AlignCenter)
        for line in ("Record a meeting or drop in an audio file.", "Everything stays on this computer."):
            empty.addWidget(label(line, "muted"), 0, Qt.AlignCenter)
        empty.addSpacing(6)
        link = Button("Open a saved transcript", None, "link")
        link.clicked.connect(self._choose_transcript)
        empty.addWidget(link, 0, Qt.AlignCenter)
        empty.addStretch(3)
        card.body.addWidget(self.empty_view, 1)

        self.result_view = QWidget()
        result = QVBoxLayout(self.result_view)
        result.setContentsMargins(0, 0, 0, 0)
        result.setSpacing(12)

        top = QHBoxLayout()
        top.setSpacing(8)
        self.title_label = ElidedLabel(role="emptyTitle")
        top.addWidget(self.title_label, 1)
        copy = Button("Copy", "copy", tooltip="Copy the transcript to the clipboard")
        copy.clicked.connect(self._copy)
        top.addWidget(copy)
        save = Button("Save as…", "save")
        save.clicked.connect(self._save_as)
        top.addWidget(save)
        self.reveal_button = Button("Show in folder", "folder", "ghost")
        self.reveal_button.clicked.connect(
            lambda: self.transcript and self.transcript.saved_path and show_in_file_manager(self.transcript.saved_path))
        top.addWidget(self.reveal_button)
        result.addLayout(top)

        meta = QHBoxLayout()
        meta.setSpacing(6)
        self.chips = QHBoxLayout()
        self.chips.setSpacing(6)
        meta.addLayout(self.chips)
        meta.addStretch(1)
        self.timestamps_check = Switch("Timestamps")
        self.timestamps_check.setChecked(True)
        self.timestamps_check.toggled.connect(self._render_text)
        meta.addWidget(self.timestamps_check)
        meta.addSpacing(10)
        self.speakers_toggle = Button("Speakers", "users", "ghost")
        self.speakers_toggle.setCheckable(True)
        self.speakers_toggle.setChecked(True)
        self.speakers_toggle.toggled.connect(lambda on: self.speakers_panel.setVisible(
            on and self.transcript is not None and self.transcript.has_speakers))
        meta.addWidget(self.speakers_toggle)
        result.addLayout(meta)
        result.addWidget(Rule())

        reading = QHBoxLayout()
        reading.setSpacing(20)
        self.text_view = QTextBrowser()
        self.text_view.setOpenLinks(False)
        self.text_view.setFrameShape(QFrame.NoFrame)
        self.text_view.document().setDocumentMargin(2)
        reading.addWidget(self.text_view, 1)

        self.speakers_panel = QFrame()
        self.speakers_panel.setObjectName("panel")
        self.speakers_panel.setFixedWidth(SPEAKERS_WIDTH)
        panel = QVBoxLayout(self.speakers_panel)
        panel.setContentsMargins(16, 16, 8, 14)
        panel.setSpacing(4)
        self.speakers_title = label("", "cardTitle")
        panel.addWidget(self.speakers_title)
        panel.addWidget(label("Type a name to replace “Speaker N” everywhere. Two speakers that are "
                              "really one person can be merged.", "caption", wrap=True))
        panel.addSpacing(8)
        self.undo_bar = QFrame()
        self.undo_bar.setObjectName("undoBar")
        undo = QHBoxLayout(self.undo_bar)
        undo.setContentsMargins(10, 6, 8, 6)
        undo.setSpacing(8)
        self.undo_label = label("", "caption", wrap=True)
        self.undo_label.setMinimumWidth(0)
        undo.addWidget(self.undo_label, 1)
        undo_button = Button("Undo", None, "link")
        undo_button.clicked.connect(self._undo_merge)
        undo.addWidget(undo_button)
        self.undo_bar.hide()
        panel.addWidget(self.undo_bar)
        panel.addSpacing(4)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.speakers_list = QWidget()
        self.speakers_layout = QVBoxLayout(self.speakers_list)
        self.speakers_layout.setContentsMargins(0, 0, 8, 0)
        self.speakers_layout.setSpacing(18)
        scroll.setWidget(self.speakers_list)
        panel.addWidget(scroll, 1)
        reading.addWidget(self.speakers_panel)
        result.addLayout(reading, 1)
        result.addWidget(Rule())
        result.addWidget(self._build_player())
        card.body.addWidget(self.result_view, 1)

        self.text_view.viewport().installEventFilter(self)
        # Fires for the user's own scrolling (wheel, keys, scroll bar), not for ours.
        self.text_view.verticalScrollBar().actionTriggered.connect(self._on_user_scroll)
        self.scroll_animation = QVariantAnimation(self)
        self.scroll_animation.setDuration(380)
        self.scroll_animation.setEasingCurve(QEasingCurve.OutCubic)
        self.scroll_animation.valueChanged.connect(
            lambda value: self.text_view.verticalScrollBar().setValue(round(value)))
        return card

    def _show_transcript(self, reload_audio: bool = True) -> None:
        transcript = self.transcript
        self.empty_view.setVisible(transcript is None)
        self.result_view.setVisible(transcript is not None)
        if transcript is None:
            return
        self.title_label.setText(transcript.source_name)
        self.title_label.setToolTip(transcript.source_path)
        self.reveal_button.setVisible(transcript.saved_path is not None)

        clear_layout(self.chips)
        chips = []
        if transcript.language:
            chips.append(language_name(transcript.language))
        if transcript.segments:
            chips.append(f"{timestamp(transcript.segments[-1].end)} long")
        if transcript.processing_time > 0:  # unknown for an opened .txt
            chips.append(f"done in {timestamp(transcript.processing_time)}")
        for text in chips:
            chip = QLabel(text)
            chip.setObjectName("chip")
            self.chips.addWidget(chip)

        clear_layout(self.speakers_layout)
        self.speaker_edits = {}
        self.speaker_avatars = {}
        self.speakers_toggle.setVisible(transcript.has_speakers)
        self.speakers_panel.setVisible(transcript.has_speakers and self.speakers_toggle.isChecked())
        self.speakers_title.setText(f"Speakers ({len(transcript.speakers)})")
        self.speakers_toggle.setText(f"Speakers ({len(transcript.speakers)})")
        total = sum(transcript.speaking_time(s) for s in transcript.speakers) or 1.0
        for speaker in transcript.speakers:
            self.speakers_layout.addWidget(self._speaker_entry(transcript, speaker, total))
        self.speakers_layout.addStretch(1)
        if reload_audio:
            self.before_merge = None
            self._load_audio(transcript)
        self.undo_bar.setVisible(self.before_merge is not None)
        self._refresh_timeline()
        self._render_text()

    def _speaker_entry(self, transcript: Transcript, speaker: int, total: float) -> QWidget:
        entry = QWidget()
        grid = QGridLayout(entry)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(5)

        name = transcript.speaker_names.get(speaker, "")
        avatar = Avatar()
        avatar.set_speaker(speaker, name)
        grid.addWidget(avatar, 0, 0, Qt.AlignTop)

        edit = QLineEdit(name)
        edit.setPlaceholderText(f"Speaker {speaker}")
        edit.textEdited.connect(lambda text, s=speaker: self._rename_speaker(s, text))
        grid.addWidget(edit, 0, 1)

        others = [s for s in transcript.speakers if s != speaker]
        if others:
            merge = Button("", "merge", "ghost", tooltip="Same person as another speaker? Merge them")
            merge.setProperty("shape", "square")
            menu = QMenu(merge)
            menu.addAction(f"{transcript.name(speaker)} is the same person as…").setEnabled(False)
            menu.addSeparator()
            for other in others:
                action = menu.addAction(self._speaker_icon(other), transcript.name(other))
                action.triggered.connect(lambda _=False, a=speaker, b=other: self._merge_speakers(a, b))
            merge.clicked.connect(lambda _=False, b=merge, m=menu: m.exec(b.mapToGlobal(b.rect().bottomLeft())))
            grid.addWidget(merge, 0, 2)

        seconds = transcript.speaking_time(speaker)
        share = seconds / total
        bar = ShareBar()
        bar.set_share(speaker, share)
        bar.setToolTip(f"Spoke for {timestamp(seconds)} ({round(share * 100)} % of the meeting)")
        grid.addWidget(bar, 1, 1, 1, 2)
        grid.addWidget(label(f"{timestamp(seconds)} · {round(share * 100)} %", "caption"), 2, 1, 1, 2)
        sample = label(f"“{transcript.sample(speaker)}”", "caption", wrap=True)
        sample.setMinimumWidth(0)
        grid.addWidget(sample, 3, 1, 1, 2)
        grid.setColumnStretch(1, 1)

        self.speaker_edits[speaker] = edit
        self.speaker_avatars[speaker] = avatar
        return entry

    def _speaker_icon(self, speaker: int) -> QIcon:
        avatar = Avatar(20)
        avatar.set_speaker(speaker, self.transcript.speaker_names.get(speaker, ""))
        return QIcon(avatar.grab())

    def _merge_speakers(self, source: int, target: int) -> None:
        """`source` is the same person as `target`: one speaker from now on."""
        if self.transcript is None:
            return
        source_name, target_name = self.transcript.name(source), self.transcript.name(target)
        self.before_merge = (copy.deepcopy(self.transcript.segments), dict(self.transcript.speaker_names))
        self.transcript.merge_speakers(source, target)
        self._save_quietly()
        self.undo_label.setText(f"Merged {source_name} into {target_name}.")
        self._show_transcript(reload_audio=False)

    def _undo_merge(self) -> None:
        if self.transcript is None or self.before_merge is None:
            return
        self.transcript.segments, self.transcript.speaker_names = self.before_merge
        self.before_merge = None
        self._save_quietly()
        self._show_transcript(reload_audio=False)

    def _transcript_html(self) -> str:
        t = theme.current()
        transcript = self.transcript
        stamps = self.timestamps_check.isChecked()
        esc = html.escape

        def words(index: int, segment) -> str:
            # The anchor marks where the segment starts, for highlighting during playback.
            return f'<a name="seg{index}">{esc(display_text(segment))}</a>'

        small = f"color:{t.muted}; font-size:12px;"
        body = f"font-size:15px; line-height:146%; margin:0 0 {22 if transcript.has_speakers else 12}px 0;"
        parts: list[str] = []
        if transcript.has_speakers:
            for index, segment in enumerate(transcript.segments):
                head = (f'<span style="color:{t.speaker_color(segment.speaker)}; font-weight:600; font-size:14px;">'
                        f"{esc(transcript.name(segment.speaker))}</span>")
                if stamps:
                    head += f'&nbsp;&nbsp;<span style="{small}">{timestamp(segment.start)}</span>'
                parts.append(f'<p style="margin:0 0 3px 0;">{head}</p>'
                             f'<p style="{body}">{words(index, segment)}</p>')
        elif stamps:
            rows = "".join(
                f'<tr><td width="74" valign="top" style="{small} padding-top:3px;">{timestamp(s.start)}</td>'
                f'<td style="font-size:15px; line-height:146%;">{words(i, s)}</td></tr>'
                f'<tr><td colspan="2" style="font-size:6px;">&nbsp;</td></tr>'
                for i, s in enumerate(transcript.segments))
            parts.append(f'<table cellspacing="0" cellpadding="0" width="100%">{rows}</table>')
        else:
            # A new paragraph after pauses over 2 s, like Transcript.text().
            paragraph: list[str] = []
            for index, segment in enumerate(transcript.segments):
                if paragraph and segment.start - transcript.segments[index - 1].end > 2:
                    parts.append(f'<p style="{body}">{" ".join(paragraph)}</p>')
                    paragraph = []
                paragraph.append(words(index, segment))
            if paragraph:
                parts.append(f'<p style="{body}">{" ".join(paragraph)}</p>')
        return "".join(parts)

    def _render_text(self) -> None:
        if self.transcript is None:
            return
        scroll = self.text_view.verticalScrollBar().value()
        self.text_view.setHtml(self._transcript_html())
        self.text_view.verticalScrollBar().setValue(scroll)
        self._index_text()
        self._highlighted = None
        self._highlight(self.player.position)

    def _rename_speaker(self, speaker: int, name: str) -> None:
        """Gives a speaker a real name and updates the saved transcript file."""
        if self.transcript is None:
            return
        self.transcript.speaker_names[speaker] = name
        if speaker in self.speaker_avatars:
            self.speaker_avatars[speaker].set_speaker(speaker, name)
        self._render_text()
        self._save_quietly()

    def _copy(self) -> None:
        if self.transcript:
            QGuiApplication.clipboard().setText(self.transcript.text(self.timestamps_check.isChecked()))

    def _save_as(self) -> None:
        if not self.transcript:
            return
        name = os.path.splitext(self.transcript.source_name)[0] + ".txt"
        path, _ = QFileDialog.getSaveFileName(self, "Save transcript", os.path.join(paths.transcripts(), name),
                                              "Text files (*.txt)")
        if not path:
            return
        try:
            self._save(self.transcript, path)
        except OSError as error:
            self._show_error(f"Couldn't save: {error}")

    # MARK: - Playback

    def _build_player(self) -> QWidget:
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 2, 0, 0)
        row.setSpacing(12)
        self.play_button = Button("", "play", "primary")
        self.play_button.setProperty("shape", "round")
        self.play_button.clicked.connect(self._toggle_playback)
        row.addWidget(self.play_button)
        self.play_time = label(timestamp(0), "rowTitle")
        self.play_time.setFont(theme.tabular(self.play_time.font()))
        row.addWidget(self.play_time)
        self.scrubber = Scrubber()
        self.scrubber.seeking.connect(self._on_scrubbing)
        self.scrubber.seeked.connect(self._seek)
        row.addWidget(self.scrubber, 1)
        self.play_duration = label(timestamp(0), "muted")
        self.play_duration.setFont(theme.tabular(self.play_duration.font()))
        row.addWidget(self.play_duration)
        row.addSpacing(4)
        self.player_hint = label("", "caption")
        row.addWidget(self.player_hint)
        self.follow_button = Button("Back to playback", None, "link",
                                    tooltip="Scroll back to what is being played")
        self.follow_button.clicked.connect(self._resume_follow)
        row.addWidget(self.follow_button)
        self.find_audio_button = Button("Find audio file…", None, "link",
                                        tooltip="Choose the recording this transcript was made from")
        self.find_audio_button.clicked.connect(self._choose_audio)
        row.addWidget(self.find_audio_button)
        return bar

    def _load_audio(self, transcript: Transcript) -> None:
        self.play_timer.stop()
        self._playhead_shown = False
        self._follow = True
        end = transcript.segments[-1].end if transcript.segments else 0.0
        self.player_error = None
        self._highlighted = None
        if not os.path.isfile(transcript.source_path):
            self.player.close()
            self.player_error = f"“{transcript.source_name}” wasn't found at {os.path.dirname(transcript.source_path)}."
        else:
            try:
                self.player.open(transcript.source_path, end)
            except PlayerError as error:
                self.player.close()
                self.player_error = str(error)
        self._refresh_timeline()
        self.scrubber.set_position(0.0)
        self.play_duration.setText(timestamp(self.player.duration or end))
        self.play_time.setText(timestamp(0))
        self._refresh_player()

    def _refresh_timeline(self) -> None:
        transcript = self.transcript
        if transcript is None:
            return
        end = transcript.segments[-1].end if transcript.segments else 0.0
        turns = [(s.start, s.end, s.speaker) for s in transcript.segments] if transcript.has_speakers else []
        self.scrubber.set_duration(self.player.duration or end, turns)

    def _refresh_player(self) -> None:
        available = self.player.path is not None
        playing = self.player.playing
        self.play_button.setEnabled(available and self.recorder.state == IDLE)
        self.scrubber.setEnabled(available)
        self.play_button.set_icon_name("pause" if playing else "play")
        self.play_button.setToolTip("Pause" if playing else "Play the recording")
        following = self._follow or not self._playhead_shown
        self.follow_button.setVisible(available and not following)
        self.find_audio_button.setVisible(not available and self.transcript is not None)
        # Short texts only: a long file name here would widen the whole card.
        if not available:
            missing = self.transcript is not None and not os.path.isfile(self.transcript.source_path)
            self.player_hint.setText("Recording not found" if missing else "Can't play the recording")
            self.player_hint.setToolTip(self.player_error or "")
        else:
            self.player_hint.setText("Click the text to play from there")
            self.player_hint.setToolTip("")
        self.player_hint.setVisible(not available or following)
        if playing:
            self.play_timer.start()
        else:
            self.play_timer.stop()

    def _toggle_playback(self) -> None:
        if self.player.playing:
            self.player.pause()
        else:
            try:
                self.player.play()
            except PlayerError as error:
                self._show_error(str(error))
            self._playhead_shown = True
            self._follow = True
        self._refresh_player()
        self._show_position(self.player.position)

    def _seek(self, seconds: float) -> None:
        """Jumps to `seconds`, playing on from there if playback was running."""
        try:
            self.player.seek(seconds)
        except PlayerError as error:
            self._show_error(str(error))
        self._playhead_shown = True
        self._follow = True
        self._refresh_player()
        self._show_position(seconds)

    def _on_scrubbing(self, seconds: float) -> None:
        # Preview where the drag would land; the audio jumps on release.
        self._playhead_shown = True
        self._follow = True
        self._show_position(seconds)

    def _on_play_tick(self) -> None:
        if self.player.ended:
            self.player.pause()
            self._refresh_player()
        self._show_position(self.player.position)

    def _show_position(self, seconds: float) -> None:
        self.play_time.setText(timestamp(seconds))
        self.scrubber.set_position(seconds)
        self._highlight(seconds)
        self._scroll_to_playback(seconds)

    def _index_text(self) -> None:
        """Finds where each segment, and each timed word, sits in the view."""
        starts: dict[int, int] = {}
        block = self.text_view.document().begin()
        while block.isValid():
            fragments = block.begin()
            while not fragments.atEnd():
                fragment = fragments.fragment()
                for name in fragment.charFormat().anchorNames():
                    if name.startswith("seg"):
                        starts[int(name[3:])] = fragment.position()
                fragments += 1
            block = block.next()

        self.segment_spans, self.word_spans = [], []
        for index, segment in enumerate(self.transcript.segments if self.transcript else []):
            text = display_text(segment)
            first = starts.get(index, self.segment_spans[-1][1] if self.segment_spans else 0)
            self.segment_spans.append((first, first + len(text)))
            words, offset = [], 0
            for word in segment.words:
                found = text.find(word.text, offset) if word.text else -1
                if found >= 0:
                    words.append((word.start, word.end, first + found, first + found + len(word.text)))
                    offset = found + len(word.text)
            self.word_spans.append(words)

    def _playing_at(self, seconds: float) -> tuple[int | None, int | None]:
        """The segment and word being spoken at `seconds`. In a pause, the
        last thing said stays current."""
        if self.transcript is None or not self._playhead_shown:
            return None, None
        segment = bisect.bisect_right([s.start for s in self.transcript.segments], seconds) - 1
        if segment < 0 or segment >= len(self.segment_spans):
            return None, None
        words = self.word_spans[segment]
        word = bisect.bisect_right([w[0] for w in words], seconds) - 1
        return segment, (word if word >= 0 else None)

    def _highlight(self, seconds: float) -> None:
        segment, word = self._playing_at(seconds)
        if (segment, word) == self._highlighted:
            return
        self._highlighted = (segment, word)
        t = theme.current()
        selections = []
        if segment is not None:
            selections.append(self._selection(*self.segment_spans[segment], t.brand_soft))
            if word is not None:
                _, _, first, last = self.word_spans[segment][word]
                selections.append(self._selection(first, last, theme.mix(t.brand_soft, t.brand, 0.42 if t.dark else 0.32)))
        self.text_view.setExtraSelections(selections)

    def _selection(self, first: int, last: int, background) -> QTextEdit.ExtraSelection:
        cursor = QTextCursor(self.text_view.document())
        cursor.setPosition(first)
        cursor.setPosition(last, QTextCursor.KeepAnchor)
        char_format = QTextCharFormat()
        char_format.setBackground(QColor(background))
        char_format.setForeground(QColor(theme.current().ink))
        selection = QTextEdit.ExtraSelection()
        selection.cursor = cursor
        selection.format = char_format
        return selection

    def _scroll_to_playback(self, seconds: float) -> None:
        """Keeps what is being said in the middle of the view. Near the start
        and end of the transcript the scroll range runs out, so it sits higher
        or lower instead."""
        if not self._follow:
            return
        segment, word = self._playing_at(seconds)
        if segment is None:
            return
        first, last = self.segment_spans[segment]
        if word is not None:
            position = self.word_spans[segment][word][2]
        else:
            # Without word timings, move through the segment at an even pace.
            s = self.transcript.segments[segment]
            progress = (seconds - s.start) / (s.end - s.start) if s.end > s.start else 0.0
            position = first + round((last - first) * max(0.0, min(1.0, progress)))
        cursor = QTextCursor(self.text_view.document())
        cursor.setPosition(position)
        bar = self.text_view.verticalScrollBar()
        line = self.text_view.cursorRect(cursor)
        target = bar.value() + line.center().y() - self.text_view.viewport().height() // 2
        target = max(bar.minimum(), min(bar.maximum(), target))
        animating = self.scroll_animation.state() == QVariantAnimation.Running
        if abs(target - (self.scroll_animation.endValue() if animating else bar.value())) < 2:
            return
        self.scroll_animation.stop()
        self.scroll_animation.setStartValue(bar.value())
        self.scroll_animation.setEndValue(target)
        self.scroll_animation.start()

    def _on_user_scroll(self, _action) -> None:
        """Reading elsewhere while it plays: stop pulling the view back."""
        if self._playhead_shown and self._follow:
            self._follow = False
            self.scroll_animation.stop()
            self._refresh_player()

    def _resume_follow(self) -> None:
        self._follow = True
        self._refresh_player()
        self._scroll_to_playback(self.player.position)

    def _seconds_at_text(self, point) -> float | None:
        """The time to play from for a click in the transcript: the word
        clicked, or the start of its line (also for a click on the speaker
        name or timestamp above it)."""
        if self.transcript is None or not self.segment_spans:
            return None
        position = self.text_view.cursorForPosition(point).position()
        segment = bisect.bisect_left([last for _, last in self.segment_spans], position)
        if segment >= len(self.segment_spans):
            return None
        words = self.word_spans[segment]
        word = bisect.bisect_right([w[2] for w in words], position) - 1
        if word >= 0 and position >= self.segment_spans[segment][0]:
            return words[word][0]
        return self.transcript.segments[segment].start

    def eventFilter(self, watched, event) -> bool:
        if watched is self.text_view.viewport() and self.player.path is not None:
            # A drag selects text to copy; only a plain click jumps.
            if (event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton
                    and not self.text_view.textCursor().hasSelection()):
                seconds = self._seconds_at_text(event.position().toPoint())
                if seconds is not None:
                    self._seek(seconds)
        return super().eventFilter(watched, event)

    # MARK: - Misc

    def _show_error(self, message: str) -> None:
        box = QMessageBox(QMessageBox.NoIcon, "Something went wrong", message, QMessageBox.Ok, self)
        box.exec()

    def closeEvent(self, event) -> None:
        if self.recorder.state != IDLE:
            box = QMessageBox(QMessageBox.NoIcon, "Stop recording?",
                              "A recording is in progress. Stop and save it before quitting?",
                              QMessageBox.NoButton, self)
            stop = box.addButton("Stop and quit", QMessageBox.AcceptRole)
            box.addButton("Keep recording", QMessageBox.RejectRole)
            box.setDefaultButton(stop)
            box.exec()
            if box.clickedButton() is not stop:
                event.ignore()
                return
            try:
                self.recorder.stop()
            except RecorderError:
                pass
        if self.cancel_flag is not None:
            self.cancel_flag.set()
        self.player.close()
        self.settings.setValue("windowGeometry", self.saveGeometry())
        super().closeEvent(event)


def main() -> int:
    if sys.platform == "win32":
        # Own taskbar icon instead of the Python one.
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("se.leoslyssnare.app")
        except Exception:
            pass
    app = QApplication(sys.argv)
    app.setApplicationName("Leos Lyssnare")
    app.setDesktopFileName("leoslyssnare")
    icon = resource_path("icon.png")
    if os.path.exists(icon):
        app.setWindowIcon(QIcon(icon))
    setup_application(app)
    window = MainWindow()
    window.show()
    return app.exec()
