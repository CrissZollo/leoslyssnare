"""Checks the start-up of the packaged app that doesn't need a window."""

import sys

from leoslyssnare.__main__ import _ensure_output_streams


def test_windowed_build_gets_output_streams(monkeypatch):
    """A windowed Windows build starts with no stdout or stderr. Downloading a
    speech model used to fail there, because its progress bar writes to stderr."""
    from huggingface_hub.utils import tqdm

    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    _ensure_output_streams()
    print("goes nowhere")
    with tqdm(total=10, desc="Fetching files") as bar:
        bar.update(10)
    assert sys.stdout is not None and sys.stderr is not None
