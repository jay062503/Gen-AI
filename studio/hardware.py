"""Hardware detection and per-tier recommendations.

Detection avoids importing torch unless it is installed, and never raises:
on any problem it degrades to "cpu / unknown".
"""
from __future__ import annotations

import importlib.util
import platform
import shutil
import sys
from functools import lru_cache


def _has(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def _ram_gb() -> float:
    try:
        import psutil

        return round(psutil.virtual_memory().total / 1024**3, 1)
    except Exception:
        return 0.0


@lru_cache(maxsize=1)
def detect() -> dict:
    system = platform.system()
    machine = platform.machine().lower()
    ram = _ram_gb()
    info = {
        "os": system,
        "arch": machine,
        "python": sys.version.split()[0],
        "ram_gb": ram,
        "torch": None,
        "diffusers": None,
        "device": "cpu",
        "device_name": platform.processor() or machine,
        "memory_gb": ram,  # memory usable by a model (VRAM, or unified RAM on Apple Silicon)
        "apple_silicon": system == "Darwin" and machine in {"arm64", "aarch64"},
        "ml_installed": _has("torch") and _has("diffusers") and _has("transformers"),
    }
    if info["ml_installed"]:
        try:
            import diffusers
            import torch

            info["torch"] = torch.__version__
            info["diffusers"] = diffusers.__version__
            if torch.cuda.is_available():
                props = torch.cuda.get_device_properties(0)
                info["device"] = "cuda"
                info["device_name"] = props.name
                info["memory_gb"] = round(props.total_memory / 1024**3, 1)
            elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
                info["device"] = "mps"
                info["device_name"] = "Apple Silicon GPU (MPS)"
        except Exception:  # broken torch install
            info["ml_installed"] = False
    elif info["apple_silicon"]:
        info["device_name"] = "Apple Silicon (PyTorch not installed yet)"
    return info


def resolve_device(configured: str = "auto") -> str:
    if configured and configured != "auto":
        return configured
    return detect()["device"]


def tier(hw: dict | None = None) -> str:
    """demo < compact < standard < large, from usable accelerator memory."""
    hw = hw or detect()
    if hw["device"] == "cpu":
        return "demo"
    mem = hw["memory_gb"]
    if mem >= 48:
        return "large"
    if mem >= 24:
        return "standard"
    if mem >= 15:
        return "compact"
    return "demo"


# Conservative starting points per tier (all overridable in .env).
TIER_LIMITS = {
    "demo":     {"image_default": 512,  "video_default": (512, 320), "video_frames": 25,  "low_memory": True},
    "compact":  {"image_default": 768,  "video_default": (512, 320), "video_frames": 49,  "low_memory": True},
    "standard": {"image_default": 1024, "video_default": (704, 480), "video_frames": 97,  "low_memory": False},
    "large":    {"image_default": 1024, "video_default": (704, 480), "video_frames": 121, "low_memory": False},
}


def free_disk_gb(path) -> float:
    try:
        return round(shutil.disk_usage(path).free / 1024**3, 1)
    except Exception:
        return 0.0
