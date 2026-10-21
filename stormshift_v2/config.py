"""Runtime configuration. All mutable data stays inside the remote project."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    root: Path = ROOT
    data_dir: Path = Path(os.getenv("STORMSHIFT_DATA_DIR", ROOT / "data"))
    cache_dir: Path = Path(os.getenv("STORMSHIFT_CACHE_DIR", ROOT / ".cache"))
    temp_dir: Path = ROOT / "tmp"
    frontend_dist: Path = ROOT / "frontend" / "dist"
    legacy_web: Path = ROOT / "web"
    fast_model: str = os.getenv("STORMSHIFT_FAST_MODEL", "Qwen/Qwen3-8B")
    deep_model: str = os.getenv("STORMSHIFT_DEEP_MODEL", "Qwen/Qwen3-30B-A3B")
    fast_base_url: str = os.getenv("STORMSHIFT_FAST_BASE_URL", "http://127.0.0.1:8101/v1")
    deep_base_url: str = os.getenv("STORMSHIFT_DEEP_BASE_URL", "http://127.0.0.1:8102/v1")
    llm_mode: str = os.getenv("STORMSHIFT_ENABLE_LLM", "auto")
    voice_mode: str = os.getenv("STORMSHIFT_ENABLE_VOICE", "auto")
    opensky_client_id: str = os.getenv("OPENSKY_CLIENT_ID", "")
    opensky_client_secret: str = os.getenv("OPENSKY_CLIENT_SECRET", "")

    @property
    def database_path(self) -> Path:
        return self.data_dir / "stormshift.db"

    def ensure_directories(self) -> None:
        for path in (self.data_dir, self.cache_dir, self.temp_dir):
            path.mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.ensure_directories()
