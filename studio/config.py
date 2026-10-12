"""Runtime configuration, read from environment variables / .env.

Settings are built by ``load_settings()`` rather than at import time so tests
and embedding applications (e.g. Enfactum AI) can construct their own.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]

# Must be set before torch is imported anywhere: lets PyTorch fall back to the
# CPU for the few ops Apple's MPS backend does not implement yet.
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def _get(name: str, default: str = "", *legacy: str) -> str:
    for key in (name, *legacy):
        val = os.getenv(key)
        if val is not None and val.strip() != "":
            return val.strip()
    return default


def _bool(name: str, default: bool, *legacy: str) -> bool:
    raw = _get(name, "", *legacy).lower()
    if not raw:
        return default
    return raw not in {"0", "false", "no", "off"}


def _int(name: str, default: int, lo: int | None = None, hi: int | None = None) -> int:
    try:
        val = int(_get(name, str(default)))
    except ValueError:
        val = default
    if lo is not None:
        val = max(lo, val)
    if hi is not None:
        val = min(hi, val)
    return val


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 8090
    data_dir: Path = ROOT / "data"
    model_cache: Path = ROOT / "data" / "models"

    # Auth. Empty token = open (only allowed on loopback).
    api_token: str = ""
    allow_insecure: bool = False
    cors_origins: tuple = ()
    callback_hosts: tuple = ()

    # Inference
    device: str = "auto"            # auto | cuda | mps | cpu
    dtype: str = "auto"             # auto | float16 | bfloat16 | float32
    image_backend: str = "auto"     # auto | flux2 | mock
    video_backend: str = "auto"     # auto | ltx-video | ltx-2.5 | mock
    image_model_id: str = "black-forest-labs/FLUX.2-klein-4B"
    video_model_id: str = "Lightricks/LTX-Video"
    ltx25_model_id: str = "Lightricks/LTX-2.5-Diffusers"
    low_memory: bool | None = None  # None = decide from detected hardware

    # Queue / limits
    max_concurrent_heavy: int = 1
    max_concurrent_light: int = 2
    queue_size: int = 32
    max_image_upload_mb: int = 25
    max_video_upload_mb: int = 300
    max_image_pixels: int = 4_194_304
    max_video_pixels: int = 921_600
    max_video_frames: int = 241
    retention_days: int = 0         # 0 = keep everything

    @property
    def is_loopback(self) -> bool:
        return self.host in LOOPBACK

    def with_(self, **kw) -> "Settings":
        return replace(self, **kw)


def load_settings() -> Settings:
    load_dotenv(ROOT / ".env")
    data_dir = Path(_get("MEDIA_DATA_DIR", str(ROOT / "data"))).expanduser().resolve()
    cache = Path(_get("MEDIA_MODEL_CACHE", str(data_dir / "models"))).expanduser().resolve()
    low = _get("MEDIA_LOW_MEMORY", "").lower()
    return Settings(
        host=_get("MEDIA_HOST", "127.0.0.1"),
        port=_int("MEDIA_PORT", 8090, 1, 65535),
        data_dir=data_dir,
        model_cache=cache,
        # MEDIA_INTERNAL_TOKEN is the name used by the first prototype.
        api_token=_get("MEDIA_API_TOKEN", "", "MEDIA_INTERNAL_TOKEN"),
        allow_insecure=_bool("MEDIA_ALLOW_INSECURE", False),
        cors_origins=tuple(x.strip() for x in _get("MEDIA_CORS_ORIGINS").split(",") if x.strip()),
        callback_hosts=tuple(x.strip().lower() for x in _get("MEDIA_CALLBACK_HOSTS").split(",") if x.strip()),
        device=_get("MEDIA_DEVICE", "auto").lower(),
        dtype=_get("MEDIA_DTYPE", "auto").lower(),
        image_backend=_get("MEDIA_IMAGE_BACKEND", "auto").lower(),
        video_backend=_get("MEDIA_VIDEO_BACKEND", "auto").lower(),
        image_model_id=_get("IMAGE_MODEL_ID", "black-forest-labs/FLUX.2-klein-4B"),
        video_model_id=_get("VIDEO_MODEL_ID", "Lightricks/LTX-Video"),
        ltx25_model_id=_get("LTX25_MODEL_ID", "Lightricks/LTX-2.5-Diffusers"),
        low_memory=None if low == "" else low not in {"0", "false", "no", "off"},
        max_concurrent_heavy=_int("MEDIA_MAX_CONCURRENT_HEAVY_JOBS", 1, 1, 8),
        max_concurrent_light=_int("MEDIA_MAX_CONCURRENT_LIGHT_JOBS", 2, 1, 8),
        queue_size=_int("MEDIA_QUEUE_SIZE", 32, 1, 1000),
        max_image_upload_mb=_int("MEDIA_MAX_UPLOAD_MB", 25, 1, 500),
        max_video_upload_mb=_int("MEDIA_MAX_VIDEO_UPLOAD_MB", 300, 1, 4000),
        max_image_pixels=_int("MEDIA_MAX_IMAGE_PIXELS", 4_194_304, 65_536),
        max_video_pixels=_int("MEDIA_MAX_VIDEO_PIXELS", 921_600, 65_536),
        max_video_frames=_int("MEDIA_MAX_VIDEO_FRAMES", 241, 9, 1000),
        retention_days=_int("MEDIA_RETENTION_DAYS", 0, 0),
    )
