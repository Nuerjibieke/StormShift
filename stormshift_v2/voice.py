"""Short-lived, server-local speech transcription via faster-whisper."""

from __future__ import annotations

import asyncio
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from .config import Settings, settings

_model: Any = None
_model_lock = threading.Lock()


def _load_model(config: Settings):
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is not None:
            return _model
        from faster_whisper import WhisperModel

        model_name = os.getenv("STORMSHIFT_WHISPER_MODEL", "small")
        device = os.getenv("STORMSHIFT_WHISPER_DEVICE", "auto")
        compute_type = os.getenv("STORMSHIFT_WHISPER_COMPUTE", "default")
        _model = WhisperModel(
            model_name,
            device=device,
            compute_type=compute_type,
            download_root=str(config.cache_dir / "whisper"),
        )
    return _model


def _transcribe(path: Path, config: Settings) -> dict[str, Any]:
    model = _load_model(config)
    segments, info = model.transcribe(
        str(path),
        beam_size=3,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 350},
    )
    text = "".join(segment.text for segment in segments).strip()
    return {
        "text": text,
        "language": info.language,
        "language_confidence": round(float(info.language_probability), 4),
        "duration_seconds": round(float(info.duration), 2),
        "engine": "faster-whisper",
    }


async def transcribe_bytes(
    content: bytes,
    filename: str,
    config: Settings = settings,
) -> dict[str, Any]:
    if not content:
        raise ValueError("音频为空")
    if len(content) > 12 * 1024 * 1024:
        raise ValueError("音频超过 12 MB")
    suffix = Path(filename or "speech.webm").suffix.lower()
    if suffix not in {".webm", ".wav", ".mp3", ".m4a", ".ogg", ".mp4"}:
        suffix = ".webm"
    descriptor, name = tempfile.mkstemp(
        prefix="stormshift-voice-",
        suffix=suffix,
        dir=config.temp_dir,
    )
    path = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
        return await asyncio.to_thread(_transcribe, path, config)
    finally:
        path.unlink(missing_ok=True)
