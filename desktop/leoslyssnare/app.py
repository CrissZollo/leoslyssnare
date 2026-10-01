"""The main window. Same layout and behaviour as the macOS app's ContentView."""

from __future__ import annotations

import os
import subprocess
import sys
import threading

from PySide6.QtCore import QObject, QSettings, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont, QGuiApplication, QIcon
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, QFrame,
                               QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
                               QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
                               QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from . import engine, keepawake, paths
from .recorder import IDLE, PAUSED, RECORDING, AudioRecorder, RecorderError
from .transcript import Transcript, timestamp

AUDIO_EXTENSIONS = (".m4a", ".mp3", ".wav", ".aac", ".flac", ".ogg", ".opus", ".mp4", ".mov",
                    ".webm", ".wma", ".aiff", ".aif", ".caf")


def resource_path(name: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources", name)


def open_folder(path: str) -> None:
    QDesktopServices.openUrl(QUrl.fromLocalFile(path))


def show_in_file_manager(path: str) -> None:
    if sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    else:
        open_folder(os.path.dirname(path))


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


class DropArea(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("dropArea")
        self.setMinimumHeight(90)
        self._set_targeted(False)
        layout = QVBoxLayout(self)
        icon = QLabel("〰")
        icon.setAlignment(Qt.AlignCenter)
        icon.setStyleSheet("font-size: 28px; color: palette(placeholder-text); border: none;")
        text = QLabel("Drop an audio file here (.m4a, .mp3, .wav…)")
        text.setAlignment(Qt.AlignCenter)
        text.setStyleSheet("color: palette(placeholder-text); border: none;")
        layout.addWidget(icon)
        layout.addWidget(text)

    def _set_targeted(self, targeted: bool) -> None:
        color = "palette(highlight)" if targeted else "palette(placeholder-text)"
        self.setStyleSheet(f"#dropArea {{ border: 2px dashed {color}; border-radius: 10px; }}")


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Leos Lyssnare")
        self.setMinimumSize(940, 700)
        self.setAcceptDrops(True)

        self.settings = QSettings("LeosLyssnare", "LeosLyssnare")
        self.engine = engine.Engine()
        self.recorder = AudioRecorder()
        self.transcript: Transcript | None = None
        self.busy = False
        self.cancel_flag: engine.CancelFlag | None = None
        self.speaker_edits: dict[int, QLineEdit] = {}

        self.worker = Worker()
        self.worker.progress.connect(self._on_progress)
        self.worker.status.connect(self._on_status)
        self.worker.failed.connect(self._on_failed)
        self.worker.finished.connect(self._on_worker_finished)
        self._on_done = None
        self.worker.cancelled.connect(self._on_cancelled)

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(16)
        root.addWidget(self._build_model_section())
        cards = QHBoxLayout()
        cards.setSpacing(16)
        cards.addWidget(self._build_record_card(), 1)
        cards.addWidget(self._build_file_card(), 1)
        root.addLayout(cards)
        root.addWidget(self._build_status_bar())
        root.addWidget(self._build_transcript_section(), 1)
        self.setCentralWidget(central)

        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self._tick)

        self._refresh_model_state()
        self._refresh_recorder()
        self._refresh_status(False)
        self._show_transcript()

    # MARK: - Model

    def _build_model_section(self) -> QGroupBox:
        box = QGroupBox("Speech recognition – runs on this computer")
        layout = QVBoxLayout(box)

        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(QLabel("Model"))
        self.model_combo = QComboBox()
        for option in engine.MODELS:
            self.model_combo.addItem(f"{option.name} ({option.size})", option.id)
        self._select(self.model_combo, self.settings.value("selectedModel", engine.MODELS[0].id))
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        row.addWidget(self.model_combo)

        row.addWidget(QLabel("Language"))
        self.language_combo = QComboBox()
        for code, name in engine.LANGUAGES:
            self.language_combo.addItem(name, code)
        self._select(self.language_combo, self.settings.value("language", "sv"))
        self.language_combo.currentIndexChanged.connect(
            lambda: self.settings.setValue("language", self.language_combo.currentData()))
        row.addWidget(self.language_combo)
        row.addStretch(1)

        self.offline_label = QLabel("✔ Available offline")
        self.offline_label.setStyleSheet("color: #2e9e44; font-weight: bold;")
        row.addWidget(self.offline_label)
        self.download_button = QPushButton("⬇ Download models")
        self.download_button.setToolTip("One-time download that needs internet. After that, everything runs offline.")
        self.download_button.clicked.connect(self._download_models)
        row.addWidget(self.download_button)
        layout.addLayout(row)

        row = QHBoxLayout()
        row.setSpacing(12)
        self.speakers_check = QCheckBox("Identify speakers")
        self.speakers_check.setChecked(self.settings.value("identifySpeakers", True, type=bool))
        self.speakers_check.toggled.connect(self._on_identify_toggled)
        row.addWidget(self.speakers_check)
        row.addWidget(QLabel("Number of speakers"))
        self.count_combo = QComboBox()
        self.count_combo.addItem("Detect automatically", 0)
        self.count_combo.insertSeparator(1)
        for count in range(2, 13):
            self.count_combo.addItem(str(count), count)
        self._select(self.count_combo, self.settings.value("speakerCount", 0, type=int))
        self.count_combo.currentIndexChanged.connect(
            lambda: self.settings.setValue("speakerCount", self.count_combo.currentData()))
        row.addWidget(self.count_combo)
        hint = QLabel("If you know how many people took part, choose the number. It gives better results.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(placeholder-text); font-size: 11px;")
        row.addWidget(hint, 1)
        layout.addLayout(row)

        self.model_controls = [self.model_combo, self.language_combo, self.download_button,
                               self.speakers_check, self.count_combo]
        return box

    @staticmethod
    def _select(combo: QComboBox, value) -> None:
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
        self.offline_label.setVisible(ready)
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

    def _build_record_card(self) -> QGroupBox:
        box = QGroupBox("Record a meeting")
        layout = QVBoxLayout(box)
        layout.setSpacing(14)

        row = QHBoxLayout()
        self.indicator = QLabel("●")
        row.addWidget(self.indicator)
        self.record_status = QLabel()
        self.record_status.setStyleSheet("color: palette(placeholder-text);")
        row.addWidget(self.record_status)
        row.addStretch(1)
        self.elapsed_label = QLabel(timestamp(0))
        font = QFont("monospace")
        font.setStyleHint(QFont.Monospace)
        font.setPointSize(16)
        self.elapsed_label.setFont(font)
        row.addWidget(self.elapsed_label)
        layout.addLayout(row)

        self.level_meter = QProgressBar()
        self.level_meter.setRange(0, 1000)
        self.level_meter.setTextVisible(False)
        self.level_meter.setFixedHeight(6)
        layout.addWidget(self.level_meter)

        row = QHBoxLayout()
        self.start_button = QPushButton("⏺ Start recording")
        self.start_button.setStyleSheet("QPushButton { background: #d93a3a; color: white; padding: 8px; border-radius: 6px; }"
                                        "QPushButton:disabled { background: #e8a0a0; }")
        self.start_button.clicked.connect(self._start_recording)
        self.pause_button = QPushButton("⏸ Pause")
        self.pause_button.clicked.connect(self._pause_or_resume)
        self.stop_button = QPushButton("⏹ Stop")
        self.stop_button.clicked.connect(self._stop_recording)
        for button in (self.start_button, self.pause_button, self.stop_button):
            button.setMinimumHeight(36)
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            row.addWidget(button)
        layout.addLayout(row)

        caption = QLabel("Paused parts are left out of the recording.")
        caption.setAlignment(Qt.AlignCenter)
        caption.setStyleSheet("color: palette(placeholder-text); font-size: 11px;")
        layout.addWidget(caption)
        return box

    def _start_recording(self) -> None:
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
        answer = QMessageBox.question(
            self, "Transcribe the recording?",
            f"The recording was saved as “{os.path.basename(path)}”. Do you want to transcribe it now?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if answer == QMessageBox.Yes:
            self.transcribe(path)

    def _tick(self) -> None:
        self.elapsed_label.setText(timestamp(self.recorder.elapsed))
        self.level_meter.setValue(int(self.recorder.level * 1000))
        chunk = "#d93a3a" if self.recorder.level > 0.85 else "#2e9e44"
        self.level_meter.setStyleSheet(f"QProgressBar::chunk {{ background: {chunk}; border-radius: 3px; }}"
                                       "QProgressBar { border: none; border-radius: 3px; background: palette(midlight); }")

    def _refresh_recorder(self) -> None:
        state = self.recorder.state
        self.record_status.setText({IDLE: "Ready", RECORDING: "Recording…", PAUSED: "Paused"}[state])
        color = {IDLE: "gray", RECORDING: "#d93a3a", PAUSED: "orange"}[state]
        self.indicator.setStyleSheet(f"color: {color}; font-size: 14px;")
        self.start_button.setVisible(state == IDLE)
        self.pause_button.setVisible(state != IDLE)
        self.stop_button.setVisible(state != IDLE)
        self.pause_button.setText("⏸ Pause" if state == RECORDING else "⏺ Resume")
        if state == IDLE:
            self.level_meter.setValue(0)
        self._tick()

    # MARK: - Existing file

    def _build_file_card(self) -> QGroupBox:
        box = QGroupBox("Transcribe an existing file")
        layout = QVBoxLayout(box)
        layout.setSpacing(12)
        self.drop_area = DropArea()
        layout.addWidget(self.drop_area, 1)
        self.choose_button = QPushButton("📂 Choose file…")
        self.choose_button.setMinimumHeight(36)
        self.choose_button.clicked.connect(self._choose_file)
        layout.addWidget(self.choose_button)
        return box

    def _choose_file(self) -> None:
        patterns = " ".join(f"*{ext}" for ext in AUDIO_EXTENSIONS)
        path, _ = QFileDialog.getOpenFileName(self, "Choose an audio file", "",
                                              f"Audio files ({patterns});;All files (*)")
        if path:
            self.transcribe(path)

    def _dropped_file(self, event) -> str | None:
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        files = [u.toLocalFile() for u in urls if u.isLocalFile()]
        return files[0] if files else None

    def dragEnterEvent(self, event) -> None:
        if self._dropped_file(event):
            event.acceptProposedAction()
            self.drop_area._set_targeted(True)

    def dragLeaveEvent(self, event) -> None:
        self.drop_area._set_targeted(False)

    def dropEvent(self, event) -> None:
        self.drop_area._set_targeted(False)
        path = self._dropped_file(event)
        if path:
            event.acceptProposedAction()
            self.transcribe(path)

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
            transcript.save(save_path)
            transcript.saved_path = save_path
        except OSError as error:
            self._show_error(f"Transcription finished but couldn't be saved: {error}")
        self.transcript = transcript
        self._show_transcript()

    # MARK: - Status

    def _build_status_bar(self) -> QWidget:
        self.status_widget = QWidget()
        row = QHBoxLayout(self.status_widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximumWidth(240)
        self.progress_bar.setTextVisible(False)
        row.addWidget(self.progress_bar)
        self.percent_label = QLabel()
        self.percent_label.setStyleSheet("color: palette(placeholder-text);")
        row.addWidget(self.percent_label)
        self.status_label = QLabel()
        self.status_label.setStyleSheet("color: palette(placeholder-text);")
        self.status_label.setWordWrap(True)
        row.addWidget(self.status_label, 1)
        self.cancel_button = QPushButton("Stop")
        self.cancel_button.clicked.connect(self._cancel)
        row.addWidget(self.cancel_button)
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
        self.percent_label.setVisible(self.busy and self.progress_bar.maximum() > 0)
        self.cancel_button.setVisible(self.busy and cancellable)

    def _on_progress(self, fraction: float) -> None:
        if fraction > 0:
            self.progress_bar.setRange(0, 1000)
            self.progress_bar.setValue(int(fraction * 1000))
            self.percent_label.setText(f"{int(fraction * 100)} %")
            self.percent_label.setVisible(self.busy)
        else:
            self.progress_bar.setRange(0, 0)  # indeterminate
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

    def _build_transcript_section(self) -> QGroupBox:
        box = QGroupBox("Transcript")
        layout = QVBoxLayout(box)

        # Shown before the first transcript.
        self.empty_view = QWidget()
        empty = QVBoxLayout(self.empty_view)
        empty.addStretch(1)
        label = QLabel("The transcript will appear here.")
        label.setAlignment(Qt.AlignCenter)
        label.setStyleSheet("color: palette(placeholder-text);")
        empty.addWidget(label)
        link = QPushButton("Open recordings folder")
        link.setFlat(True)
        link.setCursor(Qt.PointingHandCursor)
        link.setStyleSheet("color: palette(link); border: none;")
        link.clicked.connect(lambda: open_folder(paths.recordings()))
        empty.addWidget(link, 0, Qt.AlignCenter)
        empty.addStretch(1)
        layout.addWidget(self.empty_view)

        self.result_view = QWidget()
        result = QVBoxLayout(self.result_view)
        result.setContentsMargins(0, 0, 0, 0)

        header = QHBoxLayout()
        self.title_label = QLabel()
        self.title_label.setStyleSheet("font-weight: bold;")
        header.addWidget(self.title_label)
        self.meta_label = QLabel()
        self.meta_label.setStyleSheet("color: palette(placeholder-text);")
        header.addWidget(self.meta_label)
        header.addStretch(1)
        self.timestamps_check = QCheckBox("Timestamps")
        self.timestamps_check.setChecked(True)
        self.timestamps_check.toggled.connect(self._render_text)
        header.addWidget(self.timestamps_check)
        result.addLayout(header)

        body = QHBoxLayout()
        body.setSpacing(12)
        self.text_view = QPlainTextEdit()
        self.text_view.setReadOnly(True)
        body.addWidget(self.text_view, 1)

        self.speakers_panel = QWidget()
        self.speakers_panel.setFixedWidth(260)
        panel = QVBoxLayout(self.speakers_panel)
        panel.setContentsMargins(0, 0, 0, 0)
        self.speakers_title = QLabel()
        self.speakers_title.setStyleSheet("font-weight: bold; font-size: 14px;")
        panel.addWidget(self.speakers_title)
        hint = QLabel("Type a name to replace “Speaker N” everywhere.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(placeholder-text); font-size: 11px;")
        panel.addWidget(hint)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        self.speakers_list = QWidget()
        self.speakers_layout = QVBoxLayout(self.speakers_list)
        self.speakers_layout.setContentsMargins(0, 0, 4, 0)
        self.speakers_layout.setSpacing(12)
        scroll.setWidget(self.speakers_list)
        panel.addWidget(scroll, 1)
        body.addWidget(self.speakers_panel)
        result.addLayout(body, 1)

        buttons = QHBoxLayout()
        copy = QPushButton("Copy")
        copy.clicked.connect(self._copy)
        buttons.addWidget(copy)
        save = QPushButton("Save as…")
        save.clicked.connect(self._save_as)
        buttons.addWidget(save)
        self.reveal_button = QPushButton("Show in folder")
        self.reveal_button.clicked.connect(
            lambda: self.transcript and self.transcript.saved_path and show_in_file_manager(self.transcript.saved_path))
        buttons.addWidget(self.reveal_button)
        buttons.addStretch(1)
        recordings = QPushButton("Open recordings folder")
        recordings.clicked.connect(lambda: open_folder(paths.recordings()))
        buttons.addWidget(recordings)
        result.addLayout(buttons)
        layout.addWidget(self.result_view)
        return box

    def _show_transcript(self) -> None:
        transcript = self.transcript
        self.empty_view.setVisible(transcript is None)
        self.result_view.setVisible(transcript is not None)
        if transcript is None:
            return
        self.title_label.setText(transcript.source_name)
        meta = f"· {transcript.language} " if transcript.language else ""
        self.meta_label.setText(meta + f"· done in {timestamp(transcript.processing_time)}")
        self.reveal_button.setVisible(transcript.saved_path is not None)

        while self.speakers_layout.count():
            item = self.speakers_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.speaker_edits = {}
        self.speakers_panel.setVisible(transcript.has_speakers)
        self.speakers_title.setText(f"Speakers ({len(transcript.speakers)})")
        for speaker in transcript.speakers:
            entry = QWidget()
            column = QVBoxLayout(entry)
            column.setContentsMargins(0, 0, 0, 0)
            column.setSpacing(3)
            row = QHBoxLayout()
            edit = QLineEdit(transcript.speaker_names.get(speaker, ""))
            edit.setPlaceholderText(f"Speaker {speaker}")
            edit.textEdited.connect(lambda text, s=speaker: self._rename_speaker(s, text))
            row.addWidget(edit, 1)
            time_label = QLabel(timestamp(transcript.speaking_time(speaker)))
            time_label.setToolTip("Total speaking time")
            time_label.setStyleSheet("color: palette(placeholder-text); font-size: 11px;")
            row.addWidget(time_label)
            column.addLayout(row)
            sample = QLabel(f"“{transcript.sample(speaker)}”")
            sample.setWordWrap(True)
            sample.setStyleSheet("color: palette(placeholder-text); font-size: 11px;")
            column.addWidget(sample)
            self.speakers_layout.addWidget(entry)
            self.speaker_edits[speaker] = edit
        self.speakers_layout.addStretch(1)
        self._render_text()

    def _render_text(self) -> None:
        if self.transcript is None:
            return
        scroll = self.text_view.verticalScrollBar().value()
        self.text_view.setPlainText(self.transcript.text(self.timestamps_check.isChecked()))
        self.text_view.verticalScrollBar().setValue(scroll)

    def _rename_speaker(self, speaker: int, name: str) -> None:
        """Gives a speaker a real name and updates the saved transcript file."""
        if self.transcript is None:
            return
        self.transcript.speaker_names[speaker] = name
        self._render_text()
        if self.transcript.saved_path:
            try:
                self.transcript.save(self.transcript.saved_path)
            except OSError:
                pass

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
            self.transcript.save(path)
        except OSError as error:
            self._show_error(f"Couldn't save: {error}")

    # MARK: - Misc

    def _show_error(self, message: str) -> None:
        QMessageBox.warning(self, "Something went wrong", message)

    def closeEvent(self, event) -> None:
        if self.recorder.state != IDLE:
            answer = QMessageBox.question(
                self, "Stop recording?",
                "A recording is in progress. Stop and save it before quitting?",
                QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Yes)
            if answer != QMessageBox.Yes:
                event.ignore()
                return
            try:
                self.recorder.stop()
            except RecorderError:
                pass
        if self.cancel_flag is not None:
            self.cancel_flag.set()
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
    window = MainWindow()
    window.show()
    return app.exec()
