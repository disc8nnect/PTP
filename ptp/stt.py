"""Local speech-to-text. Tries faster-whisper, then whisper.cpp; otherwise says it is unavailable.

Nothing here calls a cloud service. The first run of a model needs a download, so run it once
while online (see README, "Before you go offline").

Environment:
  PTP_WHISPER_MODEL       faster-whisper model name or folder, default "small"
  PTP_WHISPER_DEVICE      "cpu" (default) or "cuda" for an NVIDIA GPU. Only use "cuda" after installing
                          NVIDIA cuBLAS and cuDNN for CUDA 12: without them faster-whisper can crash the
                          whole app on the first recording instead of raising an error. On the CPU it
                          uses 4 threads, so the laptop stays responsive.
  PTP_WHISPER_CLI         path to whisper.cpp "whisper-cli" (optional)
  PTP_WHISPER_MODEL_PATH  path to a ggml model file for whisper.cpp (needed with the CLI)
  PTP_STT_LANGUAGE        e.g. "tl" for Tagalog; default: let the model detect the language
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path


class STTUnavailable(RuntimeError):
    """No local speech model is installed."""


_model_cache = {}


def _faster_whisper_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def _cli_available() -> bool:
    cli = os.environ.get("PTP_WHISPER_CLI") or shutil.which("whisper-cli")
    return bool(cli and os.environ.get("PTP_WHISPER_MODEL_PATH") and shutil.which("ffmpeg"))


def backend() -> str | None:
    if _faster_whisper_available():
        return "faster-whisper"
    if _cli_available():
        return "whisper.cpp"
    return None


def transcribe(audio_path: str | Path) -> str:
    which = backend()
    language = os.environ.get("PTP_STT_LANGUAGE") or None
    if which == "faster-whisper":
        from faster_whisper import WhisperModel

        size = os.environ.get("PTP_WHISPER_MODEL", "small")
        device = os.environ.get("PTP_WHISPER_DEVICE", "cpu")
        if (size, device) not in _model_cache:
            _model_cache[size, device] = WhisperModel(size, device=device, compute_type="int8")
        segments, _info = _model_cache[size, device].transcribe(str(audio_path), language=language, vad_filter=True)
        return " ".join(seg.text.strip() for seg in segments).strip()
    if which == "whisper.cpp":
        cli = os.environ.get("PTP_WHISPER_CLI") or shutil.which("whisper-cli")
        with tempfile.TemporaryDirectory() as tmp:
            wav = os.path.join(tmp, "in.wav")
            subprocess.run(["ffmpeg", "-y", "-i", str(audio_path), "-ar", "16000", "-ac", "1", wav],
                           check=True, capture_output=True)
            cmd = [cli, "-m", os.environ["PTP_WHISPER_MODEL_PATH"], "-f", wav, "-nt",
                   "-l", language or "auto"]
            out = subprocess.run(cmd, check=True, capture_output=True, text=True)
            return out.stdout.strip()
    raise STTUnavailable(
        "No local speech model found. Install faster-whisper (pip install faster-whisper) or "
        "whisper.cpp, or paste the transcript instead.")
