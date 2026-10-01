"""The transcript data model and how it is turned into text.

This mirrors `Transcript` in the macOS app (Sources/LeosLyssnare/Transcriber.swift)
so both apps produce the same .txt files.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime


@dataclass
class Word:
    start: float
    end: float
    text: str


@dataclass
class Segment:
    """One line of the transcript. With speaker detection on, each line is a
    speaker turn: consecutive speech by the same person is merged together."""

    start: float
    end: float
    # 1-based, numbered in the order people first speak. None means unknown.
    speaker: int | None
    text: str
    # When each word is said, for following along during playback. Only known
    # with speaker detection on; the texts appear in order in `text`.
    words: list[Word] = field(default_factory=list)


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

    def save(self, path: str, data_path: str | None = None) -> None:
        """Writes the .txt file, and with `data_path` also the exact timings
        (which the .txt rounds to whole seconds) for following along later."""
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(self.file_contents())
        if data_path:
            with open(data_path, "w", encoding="utf-8") as f:
                json.dump(self.to_data(), f, ensure_ascii=False)

    def merge_speakers(self, source: int, target: int) -> None:
        """`source` turned out to be the same person as `target`. Their lines
        become `target`'s, and lines that now follow each other are joined."""
        if source == target:
            return
        merged: list[Segment] = []
        for segment in self.segments:
            if segment.speaker == source:
                segment.speaker = target
            previous = merged[-1] if merged else None
            if previous is not None and previous.speaker == target and segment.speaker == target:
                previous.text = f"{previous.text} {segment.text}"
                previous.end = max(previous.end, segment.end)
                previous.words.extend(segment.words)
            else:
                merged.append(segment)
        self.segments = merged
        name = self.speaker_names.pop(source, "").strip()
        if name and not self.speaker_names.get(target, "").strip():
            self.speaker_names[target] = name

    def to_data(self) -> dict:
        return {
            "version": 1,
            "source_path": self.source_path,
            "language": self.language,
            "processing_time": self.processing_time,
            "has_speakers": self.has_speakers,
            "speaker_names": {str(k): v for k, v in self.speaker_names.items()},
            "segments": [{**asdict(s), "words": [[w.start, w.end, w.text] for w in s.words]}
                         for s in self.segments],
        }

    @classmethod
    def from_data(cls, data: dict) -> Transcript:
        segments = [Segment(s["start"], s["end"], s["speaker"], s["text"], [Word(*w) for w in s["words"]])
                    for s in data["segments"]]
        return cls(data["source_path"], segments, data["language"], data["processing_time"],
                   data["has_speakers"], {int(k): v for k, v in data["speaker_names"].items()})


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

        timed = Word(word.start, word.end, " ".join(word.text.split()))
        if turns and turns[-1].speaker == speaker:
            last = turns[-1]
            # Whisper words carry their own leading space.
            last.text += word.text
            last.end = word.end
            last.words.append(timed)
        else:
            turns.append(Segment(word.start, word.end, speaker, word.text, [timed]))

    for turn in turns:
        turn.text = " ".join(turn.text.split())
    return [t for t in turns if t.text]


# MARK: - Reading saved transcripts


class NotATranscript(ValueError):
    def __str__(self) -> str:
        return "This file isn't a transcript from Leos Lyssnare."


_LINE = re.compile(r"^\[(\d+):(\d{2}):(\d{2})\] ?(.*)$")
_NUMBERED = re.compile(r"^Speaker (\d+)$")


def parse(contents: str, path: str) -> Transcript:
    """Reads a transcript back from its .txt file. Times are whole seconds,
    and each line is taken to last until the next one starts."""
    lines = contents.splitlines()
    if not lines or not lines[0].startswith("Transcript of "):
        raise NotATranscript()
    source_name = lines[0][len("Transcript of "):].strip()
    language: str | None = None
    names: list[str] | None = None
    index = 1
    while index < len(lines) and lines[index].strip():
        line = lines[index]
        if found := re.search(r"· language: (\S+)", line):
            language = found.group(1)
        if line.startswith("Speakers:"):
            names = [n.strip() for n in line[len("Speakers:"):].split(",") if n.strip()]
        index += 1

    entries: list[list] = []
    for line in lines[index:]:
        if found := _LINE.match(line):
            hours, minutes, seconds = (int(found.group(i)) for i in (1, 2, 3))
            entries.append([hours * 3600 + minutes * 60 + seconds, found.group(4)])
        elif line.strip() and entries:  # a line broken by hand
            entries[-1][1] += " " + line.strip()

    has_speakers = names is not None
    numbers: dict[str, int | None] = {"Unknown": None}
    custom: dict[int, str] = {}

    def number_for(name: str) -> int | None:
        """Keeps "Speaker N" as number N; real names get the free numbers."""
        if name not in numbers:
            numbered = _NUMBERED.match(name)
            if numbered:
                numbers[name] = int(numbered.group(1))
            else:
                taken = {n for n in numbers.values() if n is not None} | {
                    int(m.group(1)) for n in names or [] if (m := _NUMBERED.match(n))}
                free = next(n for n in range(1, len(taken) + 2) if n not in taken)
                numbers[name] = free
                custom[free] = name
        return numbers[name]

    for name in names or []:
        number_for(name)
    segments: list[Segment] = []
    for start, rest in entries:
        speaker, text = None, rest
        if has_speakers:
            known = next((n for n in sorted(numbers, key=len, reverse=True) if rest.startswith(n + ":")), None)
            if known is None and ": " in rest:
                known = rest.split(": ", 1)[0]
            if known is not None:
                speaker, text = number_for(known), rest[len(known) + 1:]
        text = " ".join(text.split())
        if text:
            segments.append(Segment(start, start, speaker, text))
    for current, following in zip(segments, segments[1:] + [None]):
        # Speaking pace of about 150 words a minute for the last line.
        current.end = following.start if following else current.start + max(2.0, len(current.text.split()) / 2.5)

    source_path = os.path.join(os.path.dirname(os.path.abspath(path)), source_name)
    return Transcript(source_path, segments, language, 0.0, has_speakers, custom, saved_path=path)


def load(path: str, data_path: str | None = None) -> Transcript:
    """Opens a saved .txt transcript. If the exact timings saved alongside it
    still match the text, those are used instead of the rounded ones."""
    with open(path, encoding="utf-8-sig") as f:
        parsed = parse(f.read(), path)
    if data_path and os.path.exists(data_path):
        try:
            with open(data_path, encoding="utf-8") as f:
                exact = Transcript.from_data(json.load(f))
        except (OSError, ValueError, KeyError, TypeError):
            exact = None
        # Edited by hand since? Then the .txt is what counts.
        if exact is not None and exact.text(True) == parsed.text(True):
            exact.saved_path = path
            return exact
    return parsed
