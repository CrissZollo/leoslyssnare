"""Smoke tests for the window: it builds, shows a transcript in both themes,
and renaming a speaker updates the text. Skipped where Qt can't start."""

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")
pytest.importorskip("PySide6.QtSvg")

from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QWheelEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from leoslyssnare import app as appmod  # noqa: E402
from leoslyssnare import theme  # noqa: E402
from leoslyssnare.transcript import Segment, Transcript, Word  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    application = QApplication.instance() or QApplication([])
    theme.setup_application(application)
    return application


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))  # don't touch the user's saved settings
    result = appmod.MainWindow()
    result.show()
    yield result
    result.close()


def make_transcript(speakers: bool = True) -> Transcript:
    segments = [Segment(0, 5, 1 if speakers else None, "Hej <allihopa> & välkomna."),
                Segment(5, 9, 2 if speakers else None, "Tack för det.")]
    return Transcript("/tmp/Meeting.ogg", segments, "sv", 12.0, speakers)


def test_window_builds_and_shows_empty_state(window):
    assert window.empty_view.isVisibleTo(window)
    assert not window.result_view.isVisibleTo(window)


def test_transcript_html_escapes_text_and_uses_speaker_names(window):
    window.transcript = make_transcript()
    window.transcript.speaker_names[1] = "Anna"
    window._show_transcript()
    html = window._transcript_html()
    assert "Hej &lt;allihopa&gt; &amp; välkomna." in html
    assert "Anna" in html and "Speaker 2" in html
    assert set(window.speaker_edits) == {1, 2}


def test_rename_speaker_updates_avatar_and_text(window):
    window.transcript = make_transcript()
    window._show_transcript()
    window._rename_speaker(2, "Erik")
    assert window.speaker_avatars[2].text == "E"
    assert "Erik" in window.text_view.toPlainText()


def test_timestamps_toggle_changes_text(window):
    window.transcript = make_transcript(speakers=False)
    window._show_transcript()
    assert "00:00:05" in window.text_view.toPlainText()
    window.timestamps_check.setChecked(False)
    assert "00:00:05" not in window.text_view.toPlainText()


@pytest.mark.parametrize("dark", [False, True])
def test_both_themes_render(qapp, window, dark):
    window.transcript = make_transcript()
    window._show_transcript()
    theme.apply_scheme(qapp, dark)
    assert theme.current().dark is dark
    assert not window.grab().isNull()
    theme.apply_scheme(qapp, False)


def test_sidebar_fits_while_paused(window):
    """A long status text used to make the sidebar cards wider than their column."""
    window.recorder.state = "paused"
    window._refresh_recorder()
    sidebar = window.drop_area.parentWidget()
    scroll = sidebar.parentWidget().parentWidget()
    assert sidebar.minimumSizeHint().width() <= scroll.viewport().width()
    window.recorder.state = "idle"
    window._refresh_recorder()


def timed_transcript() -> Transcript:
    first = Segment(0, 4, 1, "Hej <allihopa>  och välkomna.",
                    [Word(0, 1, "Hej"), Word(1, 2, "<allihopa>"), Word(2, 3, "och"), Word(3, 4, "välkomna.")])
    second = Segment(5, 9, 2, "Tack för det.")
    return Transcript("/tmp/missing.ogg", [first, second], "sv", 1.0, True)


@pytest.mark.parametrize("speakers,stamps", [(True, True), (False, True), (False, False)])
def test_segments_are_found_in_the_view(window, speakers, stamps):
    window.transcript = make_transcript(speakers)
    window.timestamps_check.setChecked(stamps)
    window._show_transcript()
    text = window.text_view.toPlainText()
    for segment, (first, last) in zip(window.transcript.segments, window.segment_spans):
        assert text[first:last] == segment.text


def test_playback_highlights_word_and_segment(window):
    window.transcript = timed_transcript()
    window._show_transcript()
    text = window.text_view.toPlainText()
    assert [text[a:b] for _, _, a, b in window.word_spans[0]] == ["Hej", "<allihopa>", "och", "välkomna."]

    window._show_position(1.5)
    assert window.text_view.extraSelections() == []  # nothing until the user plays or jumps
    window._seek(1.5)
    selections = window.text_view.extraSelections()
    assert [s.cursor.selectedText() for s in selections] == ["Hej <allihopa> och välkomna.", "<allihopa>"]
    window._show_position(6)
    assert [s.cursor.selectedText() for s in window.text_view.extraSelections()] == ["Tack för det."]


def test_click_on_word_jumps_there(window):
    window.transcript = timed_transcript()
    window._show_transcript()
    first = window.word_spans[0][2][2]
    cursor = window.text_view.textCursor()
    cursor.setPosition(first + 1)
    point = window.text_view.cursorRect(cursor).center()
    assert window._seconds_at_text(point) == 2
    cursor.setPosition(window.segment_spans[1][0] + 3)
    assert window._seconds_at_text(window.text_view.cursorRect(cursor).center()) == 5


def test_view_follows_playback_until_the_user_scrolls(window, tmp_path):
    from leoslyssnare.recorder import OGG_OPUS
    from test_audio import _encode

    audio = str(tmp_path / "Meeting.ogg")
    _encode(audio, 48_000, OGG_OPUS, seconds=1.0)
    segments = [Segment(i * 5, i * 5 + 4, None, f"Line number {i}.") for i in range(200)]
    window.transcript = Transcript(audio, segments, "sv", 1.0, False)
    window._show_transcript()
    assert window.play_button.isEnabled()
    assert window.player.duration == 999  # a cut-off file is as long as its transcript
    bar = window.text_view.verticalScrollBar()
    assert bar.maximum() > 0
    window._seek(500)
    window.scroll_animation.setCurrentTime(window.scroll_animation.duration())
    cursor = window.text_view.textCursor()
    cursor.setPosition(window.segment_spans[100][0])
    middle = window.text_view.viewport().height() / 2
    assert abs(window.text_view.cursorRect(cursor).center().y() - middle) < 30

    window._seek(1)  # the start can't be centred: the view sits at the top
    window.scroll_animation.setCurrentTime(window.scroll_animation.duration())
    assert bar.value() == 0

    # Scrolling with the mouse wheel stops the view from following.
    viewport = window.text_view.viewport()
    centre = QPointF(viewport.rect().center())
    wheel = QWheelEvent(centre, viewport.mapToGlobal(centre), QPoint(), QPoint(0, -120), Qt.NoButton,
                        Qt.NoModifier, Qt.NoScrollPhase, False)
    QApplication.sendEvent(viewport, wheel)
    assert not window._follow
    assert window.follow_button.isVisibleTo(window)


def test_open_transcript_finds_its_recording_and_merges_speakers(window, tmp_path, monkeypatch):
    from leoslyssnare import paths
    from leoslyssnare.recorder import OGG_OPUS
    from test_audio import _encode

    monkeypatch.setattr(paths, "transcript_data", lambda p: str(tmp_path / (os.path.basename(p) + ".json")))
    folder = tmp_path / "meetings"
    folder.mkdir()
    _encode(str(folder / "Meeting.ogg"), 48_000, OGG_OPUS, seconds=1.0)
    segments = [Segment(0, 2, 1, "Hej.", [Word(0, 2, "Hej.")]), Segment(2, 4, 2, "Hallå."),
                Segment(4, 6, 3, "Tjena."), Segment(6, 8, 1, "Ska vi börja?")]
    saved = folder / "Meeting.txt"
    Transcript("/elsewhere/Meeting.ogg", segments, "sv", 3.0, True).save(str(saved), paths.transcript_data(str(saved)))

    window.open_file(str(saved))
    assert window.transcript.source_path == str(folder / "Meeting.ogg")
    assert window.play_button.isEnabled() and not window.find_audio_button.isVisibleTo(window)
    assert window.word_spans[0]  # exact timings came back with it
    assert set(window.speaker_edits) == {1, 2, 3}

    window._merge_speakers(3, 1)
    assert set(window.speaker_edits) == {1, 2}
    assert window.undo_bar.isVisibleTo(window)
    assert "Tjena. Ska vi börja?" in saved.read_text(encoding="utf-8")
    window._undo_merge()
    assert set(window.speaker_edits) == {1, 2, 3}
    assert not window.undo_bar.isVisibleTo(window)
    assert "Speaker 3: Tjena." in saved.read_text(encoding="utf-8")


def test_open_transcript_without_its_recording(window, tmp_path, monkeypatch):
    from leoslyssnare import paths

    monkeypatch.setattr(paths, "find_audio", lambda source, transcript: None)
    saved = tmp_path / "Lost.txt"
    make_transcript().save(str(saved))
    window.open_transcript(str(saved))
    assert not window.play_button.isEnabled()
    assert window.find_audio_button.isVisibleTo(window)
    assert window.player_hint.text() == "Recording not found"


def test_system_programs_get_the_systems_libraries(monkeypatch):
    from leoslyssnare import paths

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/opt/leoslyssnare/_internal")
    monkeypatch.setenv("QT_PLUGIN_PATH", "/opt/leoslyssnare/_internal/PySide6/Qt/plugins")
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)
    env = paths.system_env()
    assert "LD_LIBRARY_PATH" not in env and "QT_PLUGIN_PATH" not in env

    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/usr/local/lib")
    env = paths.system_env()
    assert env["LD_LIBRARY_PATH"] == "/usr/local/lib" and "LD_LIBRARY_PATH_ORIG" not in env


def test_choose_microphone_and_meeting_app(qapp, tmp_path, monkeypatch):
    from leoslyssnare import pulse

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(pulse, "available", lambda: True)
    monkeypatch.setattr(pulse, "default_microphone", lambda: "jabra")
    monkeypatch.setattr(pulse, "microphones", lambda: [pulse.Microphone("jabra", "Jabra Engage"),
                                                       pulse.Microphone("cam", "Konftel Cam10")])
    playing = [pulse.Application("teams-for-linux", "Chromium (teams-for-linux)")]
    monkeypatch.setattr(pulse, "applications", lambda: list(playing))
    window = appmod.MainWindow()
    try:
        mics = [window.mic_combo.itemText(i) for i in range(window.mic_combo.count())]
        assert mics == ["Default (Jabra Engage)", "Jabra Engage", "Konftel Cam10"]
        window.mic_combo.setCurrentIndex(2)
        window.app_combo.setCurrentIndex(window.app_combo.findData("teams-for-linux"))
        assert "headphones" in window.app_caption.text()

        # The app has stopped playing: it stays chosen, and the list refreshes when opened.
        playing.clear()
        window.app_combo.opening.emit()
        assert window.app_combo.currentText() == "Chromium (teams-for-linux)"
    finally:
        window.close()

    # Remembered next time.
    window = appmod.MainWindow()
    try:
        assert window.mic_combo.currentData() == "cam"
        assert window.app_combo.currentData() == "teams-for-linux"
    finally:
        window.close()
