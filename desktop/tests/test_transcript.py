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
