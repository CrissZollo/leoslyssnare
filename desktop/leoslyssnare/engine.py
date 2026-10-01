"""Speech to text (faster-whisper) and who spoke when (sherpa-onnx), on the CPU.

Models are downloaded once into paths.models(); after that no network is
needed. Nothing here touches the UI: progress and status are reported through
callbacks, and work is stopped through a CancelFlag. Everything runs on a
background thread.
"""

from __future__ import annotations

import os
import shutil
import tarfile
import tempfile
import threading
import time
import urllib.request
from dataclasses import dataclass
from typing import Callable

from . import paths
from .transcript import (Segment, SpeakerSpan, Transcript, Word, plain_segments,
                         speaker_turns)


@dataclass(frozen=True)
class ModelOption:
    id: str
    repo: str
    name: str
    size: str
    # Approximate download size, for the progress bar.
    bytes: int


MODELS = [
    ModelOption("large-v3-turbo", "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
                "Large v3 Turbo – best quality", "≈1.6 GB", 1_620_000_000),
    ModelOption("small", "Systran/faster-whisper-small", "Small – faster", "≈480 MB", 484_000_000),
    ModelOption("base", "Systran/faster-whisper-base", "Base – fastest, lower quality", "≈150 MB", 145_000_000),
]

AUTO_LANGUAGE = "auto"
LANGUAGES = [
    (AUTO_LANGUAGE, "Auto-detect"),
    ("sv", "Swedish"),
    ("en", "English"),
    ("no", "Norwegian"),
    ("da", "Danish"),
    ("fi", "Finnish"),
    ("de", "German"),
    ("fr", "French"),
    ("es", "Spanish"),
]

# Speaker diarization models from the sherpa-onnx project (no account needed).
SEGMENTATION_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/"
                    "speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2")
EMBEDDING_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/"
                 "speaker-recongition-models/wespeaker_en_voxceleb_resnet34_LM.onnx")

SAMPLE_RATE = 16_000
_COMPLETE_MARKER = ".complete"

Progress = Callable[[float], None]
Status = Callable[[str], None]


class ModelMissing(Exception):
    def __str__(self) -> str:
        return ("The models haven't been downloaded yet. Click “Download models” once while you're "
                "online. After that, everything works fully offline.")


class Cancelled(Exception):
    pass


class CancelFlag:
    def __init__(self) -> None:
        self._event = threading.Event()

    def set(self) -> None:
        self._event.set()

    @property
    def is_set(self) -> bool:
        return self._event.is_set()


def model_option(model_id: str) -> ModelOption:
    return next((m for m in MODELS if m.id == model_id), MODELS[0])


# MARK: - Model locations


def whisper_dir(model_id: str) -> str:
    return os.path.join(paths.models(), "whisper", model_id)


def speaker_dir() -> str:
    return os.path.join(paths.models(), "speakers")


def segmentation_model() -> str:
    return os.path.join(speaker_dir(), "segmentation.onnx")


def embedding_model() -> str:
    return os.path.join(speaker_dir(), "embedding.onnx")


def whisper_ready(model_id: str) -> bool:
    return os.path.exists(os.path.join(whisper_dir(model_id), _COMPLETE_MARKER))


def speaker_models_ready() -> bool:
    return os.path.exists(segmentation_model()) and os.path.exists(embedding_model())


def _folder_size(folder: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(folder):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


# MARK: - Downloading (the only part that needs internet)


def download_whisper(model_id: str, progress: Progress) -> None:
    from huggingface_hub import snapshot_download

    option = model_option(model_id)
    target = whisper_dir(model_id)
    os.makedirs(target, exist_ok=True)

    # huggingface_hub doesn't report byte progress for one big file, so watch
    # the folder grow instead.
    done = threading.Event()

    def watch() -> None:
        while not done.wait(0.5):
            progress(min(0.99, _folder_size(target) / option.bytes))

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    try:
        snapshot_download(
            option.repo,
            local_dir=target,
            allow_patterns=["config.json", "preprocessor_config.json", "model.bin",
                            "tokenizer.json", "vocabulary.*"],
        )
    finally:
        done.set()
        watcher.join()
    progress(1.0)


def _fetch(url: str, destination: str, progress: Progress) -> None:
    import certifi
    import ssl

    # SSL_CERT_FILE lets it work behind a proxy with its own certificate.
    context = ssl.create_default_context(cafile=os.environ.get("SSL_CERT_FILE") or certifi.where())
    request = urllib.request.Request(url, headers={"User-Agent": "LeosLyssnare"})
    with urllib.request.urlopen(request, context=context, timeout=60) as response:
        total = int(response.headers.get("Content-Length") or 0)
        received = 0
        with open(destination, "wb") as out:
            while chunk := response.read(1 << 16):
                out.write(chunk)
                received += len(chunk)
                if total:
                    progress(received / total)


def download_speaker_models(progress: Progress) -> None:
    os.makedirs(speaker_dir(), exist_ok=True)
    with tempfile.TemporaryDirectory(dir=speaker_dir()) as work:
        archive = os.path.join(work, "segmentation.tar.bz2")
        _fetch(SEGMENTATION_URL, archive, lambda f: progress(f * 0.2))
        with tarfile.open(archive, "r:bz2") as tar:
            member = next(m for m in tar.getmembers() if m.name.endswith("/model.onnx"))
            with tar.extractfile(member) as source, open(os.path.join(work, "segmentation.onnx"), "wb") as out:
                shutil.copyfileobj(source, out)

        _fetch(EMBEDDING_URL, os.path.join(work, "embedding.onnx"), lambda f: progress(0.2 + f * 0.8))

        # Move into place only once both downloads are complete.
        os.replace(os.path.join(work, "segmentation.onnx"), segmentation_model())
        os.replace(os.path.join(work, "embedding.onnx"), embedding_model())


# MARK: - Loading


class Engine:
    """Keeps loaded models around between transcriptions."""

    def __init__(self) -> None:
        self._whisper = None
        self._whisper_id: str | None = None
        self._lock = threading.Lock()

    def unload_whisper(self) -> None:
        with self._lock:
            self._whisper = None
            self._whisper_id = None

    def whisper(self, model_id: str, status: Status):
        with self._lock:
            if self._whisper is not None and self._whisper_id == model_id:
                return self._whisper
            folder = whisper_dir(model_id)
            if not os.path.exists(os.path.join(folder, "model.bin")):
                raise ModelMissing()
            status("Loading speech model…")
            from faster_whisper import WhisperModel

            # int8 on the CPU works on every Windows and Linux computer and is
            # much faster than full precision.
            model = WhisperModel(folder, device="cpu", compute_type="int8", local_files_only=True)
            self._whisper, self._whisper_id = model, model_id
            return model

    def download(self, model_id: str, with_speakers: bool, progress: Progress, status: Status) -> None:
        """One-time download of whatever is missing. Each model is also loaded
        once to check that it works offline."""
        if not whisper_ready(model_id):
            status("Downloading speech model…")
            progress(0.0)
            download_whisper(model_id, progress)
            progress(0.0)
            self.unload_whisper()
            self.whisper(model_id, status)
            open(os.path.join(whisper_dir(model_id), _COMPLETE_MARKER), "w").close()

        if with_speakers and not speaker_models_ready():
            status("Downloading speaker recognition model…")
            progress(0.0)
            download_speaker_models(progress)
            self._diarizer(0)

    @staticmethod
    def _diarizer(speaker_count: int):
        import sherpa_onnx

        if not speaker_models_ready():
            raise ModelMissing()
        config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
            segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
                pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                    model=segmentation_model()
                ),
                num_threads=max(1, (os.cpu_count() or 2) // 2),
            ),
            embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                model=embedding_model(),
                num_threads=max(1, (os.cpu_count() or 2) // 2),
            ),
            clustering=sherpa_onnx.FastClusteringConfig(
                num_clusters=speaker_count if speaker_count > 0 else -1,
                threshold=0.5,
            ),
            min_duration_on=0.3,
            min_duration_off=0.5,
        )
        if not config.validate():
            raise RuntimeError("The speaker recognition model files are damaged. Delete the Models folder and download again.")
        return sherpa_onnx.OfflineSpeakerDiarization(config)

    # MARK: - Transcription

    def transcribe(
        self,
        audio_path: str,
        model_id: str,
        language: str,
        speaker_count: int | None,
        progress: Progress,
        status: Status,
        cancel: CancelFlag,
    ) -> Transcript:
        """speaker_count None means don't identify speakers; 0 means detect automatically."""
        with_speakers = speaker_count is not None
        whisper = self.whisper(model_id, status)
        diarizer = self._diarizer(speaker_count) if with_speakers else None

        name = os.path.basename(audio_path)
        status(f"Reading “{name}”…")
        from faster_whisper.audio import decode_audio

        # 16 kHz mono samples, shared by transcription and speaker detection.
        audio = decode_audio(audio_path, sampling_rate=SAMPLE_RATE)
        duration = len(audio) / SAMPLE_RATE

        status("Step 1 of 2: Transcribing speech…" if with_speakers else f"Transcribing “{name}”…")
        progress(0.0)
        started = time.monotonic()
        pieces, info = whisper.transcribe(
            audio,
            language=None if language == AUTO_LANGUAGE else language,
            task="transcribe",
            beam_size=5,
            # Skip silences, which also stops Whisper from inventing text in them.
            vad_filter=True,
            # Word timings let speakers be matched word by word, so a speaker
            # change in the middle of a sentence is placed correctly.
            word_timestamps=with_speakers,
            condition_on_previous_text=False,
        )
        segments: list[Segment] = []
        words: list[Word] = []
        for piece in pieces:  # decoding happens while iterating
            if cancel.is_set:
                raise Cancelled()
            segments.append(Segment(piece.start, piece.end, None, piece.text))
            for word in piece.words or []:
                words.append(Word(word.start, word.end, word.word))
            if duration > 0:
                progress(min(1.0, piece.end / duration))

        if diarizer is not None:
            status("Step 2 of 2: Working out who is speaking…")
            progress(0.0)

            def on_chunk(done: int, total: int) -> int:
                if total:
                    progress(done / total)
                return 1 if cancel.is_set else 0

            result = diarizer.process(audio, callback=on_chunk)
            if cancel.is_set:
                raise Cancelled()
            spans = [SpeakerSpan(s.start, s.end, s.speaker) for s in result.sort_by_start_time()]
            lines = speaker_turns(words, spans)
        else:
            lines = plain_segments(segments)

        return Transcript(
            source_path=audio_path,
            segments=lines,
            language=info.language,
            processing_time=time.monotonic() - started,
            has_speakers=diarizer is not None,
        )
