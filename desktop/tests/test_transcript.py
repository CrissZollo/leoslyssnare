import pytest

from leoslyssnare.transcript import (Segment, SpeakerSpan, Transcript, Word, plain_segments,
                                     speaker_turns, timestamp)


def test_timestamp():
    assert timestamp(0) == "00:00:00"
    assert timestamp(3725.9) == "01:02:05"


def test_speaker_turns_merge_and_renumber():
    words = [Word(0.0, 0.4, " Hello"), Word(0.5, 0.9, " everyone."),
             Word(1.2, 1.5, " Thanks."), Word(1.6, 2.0, " Okay"), Word(2.6, 3.0, " good.")]
    # Raw ids from the diarizer are arbitrary; they get renumbered in speaking order.
    spans = [SpeakerSpan(0.0, 1.0, 7), SpeakerSpan(1.1, 2.1, 3), SpeakerSpan(2.5, 3.2, 7)]
    turns = speaker_turns(words, spans)
    assert [(t.speaker, t.text) for t in turns] == [
        (1, "Hello everyone."), (2, "Thanks. Okay"), (1, "good.")]
    assert turns[1].start == 1.2 and turns[1].end == 2.0


def test_word_in_gap_goes_to_nearest_speaker():
    words = [Word(0.0, 0.5, " a"), Word(1.9, 2.0, " b")]
    spans = [SpeakerSpan(0.0, 0.6, 0), SpeakerSpan(2.1, 3.0, 1)]
    assert [t.speaker for t in speaker_turns(words, spans)] == [1, 2]


def test_no_spans_gives_unknown_speaker():
    turns = speaker_turns([Word(0, 1, " hi")], [])
    t = Transcript("x.m4a", turns, "sv", 1.0, True)
    assert t.text(False) == "Unknown: hi"


def test_text_formats_and_renaming():
    segments = [Segment(4, 8, 1, "Welcome everyone."), Segment(9, 12, 2, "Thanks.")]
    t = Transcript("/tmp/Meeting.m4a", segments, "sv", 10, True)
    t.speaker_names[1] = "  Anna "
    assert t.text(True) == "[00:00:04] Anna: Welcome everyone.\n\n[00:00:09] Speaker 2: Thanks."
    contents = t.file_contents()
    assert contents.startswith("Transcript of Meeting.m4a\nCreated ")
    assert "· language: sv\nSpeakers: Anna, Speaker 2\n\n[00:00:04]" in contents
    assert t.speaking_time(1) == 4
    assert t.sample(2) == "Thanks."


def test_plain_paragraphs_after_pauses():
    segments = plain_segments([Segment(0, 1, None, " One."), Segment(1.5, 2, None, " Two."),
                               Segment(5, 6, None, " Three."), Segment(6, 7, None, "  ")])
    t = Transcript("a.wav", segments, None, 1, False)
    assert t.text(False) == "One. Two.\n\nThree."
    assert t.text(True) == "[00:00:00] One.\n[00:00:01] Two.\n[00:00:05] Three."


def _meeting() -> Transcript:
    segments = [Segment(1.4, 4.2, 1, "Welcome everyone.", [Word(1.4, 2.0, "Welcome"), Word(2.1, 4.2, "everyone.")]),
                Segment(5.0, 6.5, 2, "Thanks."),
                Segment(7.0, 9.0, 3, "Hi: all."),
                Segment(9.5, 11.0, 1, "Shall we start?"),
                Segment(12.0, 13.0, None, "Mm.")]
    return Transcript("/rec/Meeting.ogg", segments, "sv", 10, True, {2: "Anna Berg"})


def test_parse_reads_back_a_saved_transcript(tmp_path):
    from leoslyssnare.transcript import parse

    original = _meeting()
    path = tmp_path / "Meeting.txt"
    original.save(str(path))
    parsed = parse(path.read_text(encoding="utf-8"), str(path))
    assert parsed.text(True) == original.text(True)
    assert parsed.source_name == "Meeting.ogg" and parsed.language == "sv" and parsed.has_speakers
    assert [s.speaker for s in parsed.segments] == [1, parsed.segments[1].speaker, 3, 1, None]
    assert parsed.name(parsed.segments[1].speaker) == "Anna Berg"
    # Whole seconds, each line lasting until the next.
    assert (parsed.segments[0].start, parsed.segments[0].end) == (1, 5)
    assert parsed.saved_path == str(path)


def test_parse_plain_transcript_and_rejects_other_files(tmp_path):
    from leoslyssnare.transcript import NotATranscript, parse

    plain = Transcript("a.wav", [Segment(0, 1, None, "One."), Segment(61, 62, None, "Two.")], None, 1, False)
    parsed = parse(plain.file_contents(), str(tmp_path / "a.txt"))
    assert not parsed.has_speakers and parsed.text(True) == plain.text(True)
    with pytest.raises(NotATranscript):
        parse("Shopping list\nmilk", "list.txt")


def test_load_prefers_exact_timings_until_the_text_is_edited(tmp_path):
    from leoslyssnare.transcript import load

    path, data = tmp_path / "Meeting.txt", tmp_path / "Meeting.json"
    _meeting().save(str(path), str(data))
    exact = load(str(path), str(data))
    assert exact.segments[0].start == 1.4 and exact.segments[0].words[1].text == "everyone."
    assert exact.saved_path == str(path)

    path.write_text(path.read_text(encoding="utf-8").replace("Thanks.", "Thank you."), encoding="utf-8")
    edited = load(str(path), str(data))
    assert edited.segments[0].start == 1 and "Thank you." in edited.text(False)


def test_merge_speakers_joins_lines_and_keeps_a_name():
    t = _meeting()
    t.merge_speakers(3, 1)
    assert t.speakers == [1, 2]
    # Speaker 3's line followed speaker 1's next line, so they become one.
    assert [(s.speaker, s.text) for s in t.segments] == [
        (1, "Welcome everyone."), (2, "Thanks."), (1, "Hi: all. Shall we start?"), (None, "Mm.")]
    assert t.segments[2].end == 11.0

    t.merge_speakers(1, 2)  # into a named speaker: everything is Anna's now
    assert [(s.speaker, s.text) for s in t.segments][0] == (2, "Welcome everyone. Thanks. Hi: all. Shall we start?")
    assert t.segments[0].words[0].text == "Welcome" and t.name(2) == "Anna Berg"

    t2 = _meeting()
    t2.speaker_names = {3: "Erik"}
    t2.merge_speakers(3, 1)  # the name moves to the speaker merged into
    assert t2.name(1) == "Erik" and 3 not in t2.speaker_names


def test_batch_pacer_moves_steadily_and_waits_for_the_batch():
    from leoslyssnare.engine import BatchPacer

    shown = []
    pacer = BatchPacer(1000, shown.append)
    pacer.start_batch(0, 100, 100, now=0)  # first guess: 15 s for 100 s of speech
    pacer.show(pacer.estimate(now=7.5))
    assert shown[-1] == pytest.approx(0.05)
    pacer.show(pacer.estimate(now=600))  # much slower than guessed: stops short of the batch end
    assert 0.09 < shown[-1] < 0.1
    pacer.show(0.1)  # the batch's text arrives
    pacer.start_batch(100, 300, 200, now=20)  # the first batch took 20 s: 0.2 s per second of speech
    pacer.show(pacer.estimate(now=40))  # half of the expected 40 s: halfway through this batch
    assert shown[-1] == pytest.approx(0.2)
    pacer.show(0.12)  # never backwards
    assert shown[-1] == pytest.approx(0.2) and len(shown) == 4


def _timed(start: float, speaker: int, text: str) -> Segment:
    """A line whose words are said one per second from `start`."""
    words = [Word(start + i, start + i + 0.8, w) for i, w in enumerate(text.split())]
    return Segment(start, words[-1].end, speaker, text, words)


def test_move_text_from_the_middle_splits_the_line():
    t = Transcript("a.m4a", [_timed(0, 1, "Hello there. How are you all today?"), _timed(10, 2, "Fine.")],
                   "en", 1, True)
    text = t.segments[0].text
    first, last = text.index("How"), text.index("today") + 3  # cuts "today?" midway
    assert t.move_text((0, first), (0, last)) == 3
    assert [(s.speaker, s.text, s.start, s.end) for s in t.segments] == [
        (1, "Hello there.", 0, 1.8), (3, "How are you all today?", 2, 6.8), (2, "Fine.", 10, 10.8)]
    assert [w.text for w in t.segments[1].words] == ["How", "are", "you", "all", "today?"]


def test_move_text_keeps_the_rest_with_its_speaker():
    t = Transcript("a.m4a", [_timed(0, 1, "One two three four five")], "en", 1, True)
    t.move_text((0, 4), (0, 13), 2)  # "two three", selected with a space in front
    assert [(s.speaker, s.text, s.start, s.end) for s in t.segments] == [
        (1, "One", 0, 0.8), (2, "two three", 1, 2.8), (1, "four five", 3, 4.8)]


def test_moved_text_joins_a_neighbour_with_the_same_speaker():
    t = Transcript("a.m4a", [_timed(0, 1, "Hi Anna."), _timed(5, 2, "Hi. Shall we start?")], "en", 1, True)
    t.move_text((1, 0), (1, 3), 1)  # Anna's "Hi." was really speaker 1's
    assert [(s.speaker, s.text, s.start, s.end) for s in t.segments] == [
        (1, "Hi Anna. Hi.", 0, 5.8), (2, "Shall we start?", 6, 8.8)]
    # The rest of the line too: now speaker 2 has nothing left.
    t.move_text((1, 0), (1, len(t.segments[1].text)), 1)
    assert [(s.speaker, s.text) for s in t.segments] == [(1, "Hi Anna. Hi. Shall we start?")]
    assert t.speakers == [1]


def test_move_text_across_lines():
    t = Transcript("a.m4a", [_timed(0, 1, "Yes I agree."), _timed(4, 2, "Me too. Next item.")], "en", 1, True)
    t.move_text((0, t.segments[0].text.index("I")), (1, len("Me too.")), 3)
    assert [(s.speaker, s.text, s.start, s.end) for s in t.segments] == [
        (1, "Yes", 0, 0.8), (3, "I agree. Me too.", 1, 5.8), (2, "Next item.", 6, 7.8)]


def test_move_text_without_word_timings_splits_time_by_text():
    t = Transcript("a.m4a", [Segment(10, 29, 1, "aaaa bbbb cccc dddd")], "en", 1, True)  # a second a character
    t.move_text((0, 5), (0, 9), 2)
    assert [(s.speaker, s.text, s.start, s.end) for s in t.segments] == [
        (1, "aaaa", 10, 15), (2, "bbbb", 15, 19), (1, "cccc dddd", 19, 29)]


def test_selecting_only_spaces_moves_nothing():
    t = Transcript("a.m4a", [_timed(0, 1, "One two")], "en", 1, True)
    assert t.move_text((0, 3), (0, 4), 2) is None
    assert [(s.speaker, s.text) for s in t.segments] == [(1, "One two")]
