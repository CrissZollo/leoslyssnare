"""The transcript data model and how it is turned into text.

This mirrors `Transcript` in the macOS app (Sources/LeosLyssnare/Transcriber.swift)
so both apps produce the same .txt files.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Segment:
    """One line of the transcript. With speaker detection on, each line is a
    speaker turn: consecutive speech by the same person is merged together."""

    start: float
    end: float
    # 1-based, numbered in the order people first speak. None means unknown.
    speaker: int | None
    text: str


@dataclass
class Word:
    start: float
    end: float
    text: str


@dataclass
class SpeakerSpan:
    """A stretch of time where the diarization model heard one speaker."""

    start: float
    end: float
    speaker: int


@dataclass
class Transcript:
    source_path: str
    segments: list[Segment]
    language: str | None
    processing_time: float
    has_speakers: bool
    speaker_names: dict[int, str] = field(default_factory=dict)
    saved_path: str | None = None

    @property
    def source_name(self) -> str:
        return os.path.basename(self.source_path)

    @property
    def speakers(self) -> list[int]:
        return sorted({s.speaker for s in self.segments if s.speaker is not None})

    def name(self, speaker: int | None) -> str:
        if speaker is None:
            return "Unknown"
        custom = self.speaker_names.get(speaker, "").strip()
        return custom or f"Speaker {speaker}"

    def sample(self, speaker: int) -> str:
        """The first thing a speaker says, to help work out who it is."""
        for segment in self.segments:
            if segment.speaker == speaker:
                text = segment.text
                return text[:90] + "…" if len(text) > 90 else text
        return ""

    def speaking_time(self, speaker: int) -> float:
        """Total speaking time for a speaker, in seconds."""
        return sum(s.end - s.start for s in self.segments if s.speaker == speaker)

    def text(self, with_timestamps: bool) -> str:
        if self.has_speakers:
            lines = []
            for segment in self.segments:
                line = f"{self.name(segment.speaker)}: {segment.text}"
                lines.append(f"[{timestamp(segment.start)}] {line}" if with_timestamps else line)
            return "\n\n".join(lines)
        if with_timestamps:
            return "\n".join(f"[{timestamp(s.start)}] {s.text}" for s in self.segments)
        # Without timestamps, begin a new paragraph after pauses over 2 s.
        output = ""
        previous_end: float | None = None
        for segment in self.segments:
            if previous_end is not None:
                output += "\n\n" if segment.start - previous_end > 2 else " "
            output += segment.text
            previous_end = segment.end
        return output

    def file_contents(self, now: datetime | None = None) -> str:
        date = (now or datetime.now()).strftime("%Y-%m-%d %H:%M")
        header = f"Transcript of {self.source_name}\nCreated {date}"
        if self.language:
            header += f" · language: {self.language}"
        if self.has_speakers:
            header += "\nSpeakers: " + ", ".join(self.name(s) for s in self.speakers)
        return header + "\n\n" + self.text(with_timestamps=True) + "\n"

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(self.file_contents())


def timestamp(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 3600:02d}:{total // 60 % 60:02d}:{total % 60:02d}"


def plain_segments(segments: list[Segment]) -> list[Segment]:
    result = []
    for segment in sorted(segments, key=lambda s: s.start):
        text = segment.text.strip()
        if text:
            result.append(Segment(segment.start, segment.end, None, text))
    return result


def _speaker_for(word: Word, spans: list[SpeakerSpan]) -> int | None:
    """The speaker who overlaps the word the most, or the nearest one when the
    word falls in a gap between diarization segments."""
    best: int | None = None
    best_overlap = 0.0
    for span in spans:
        overlap = min(word.end, span.end) - max(word.start, span.start)
        if overlap > best_overlap:
            best, best_overlap = span.speaker, overlap
    if best is not None:
        return best

    middle = (word.start + word.end) / 2
    nearest: int | None = None
    nearest_distance = float("inf")
    for span in spans:
        distance = span.start - middle if middle < span.start else middle - span.end
        if distance < nearest_distance:
            nearest, nearest_distance = span.speaker, distance
    return nearest


def speaker_turns(words: list[Word], spans: list[SpeakerSpan]) -> list[Segment]:
    """Matches words to speakers, renumbers speakers in the order they first
    talk, and merges consecutive speech by the same person into one turn."""
    numbering: dict[int, int] = {}
    turns: list[Segment] = []
    for word in sorted(words, key=lambda w: w.start):
        if not word.text.strip():
            continue
        raw = _speaker_for(word, spans)
        speaker: int | None = None
        if raw is not None:
            if raw not in numbering:
                numbering[raw] = len(numbering) + 1
            speaker = numbering[raw]

        if turns and turns[-1].speaker == speaker:
            last = turns[-1]
            # Whisper words carry their own leading space.
            last.text += word.text
            last.end = word.end
        else:
            turns.append(Segment(word.start, word.end, speaker, word.text))

    for turn in turns:
        turn.text = " ".join(turn.text.split())
    return [t for t in turns if t.text]
