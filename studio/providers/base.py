from __future__ import annotations

import gc
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..media_tools import Cancel


@dataclass
class JobContext:
    """Everything a provider needs to run one job, without touching the DB."""
    job_id: str
    payload: dict
    out_dir: Path
    asset_path: Callable[[str], Path]
    report: Callable[[float, str], None]
    is_cancelled: Callable[[], bool]
    extra: dict = field(default_factory=dict)

    def progress(self, frac: float, message: str = "") -> None:
        self.report(frac, message)

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled():
            raise Cancel()


def pick_seed(payload: dict) -> int:
    seed = payload.get("seed")
    return int(seed) if seed is not None else random.randint(0, 2**32 - 2**8)


class Provider(ABC):
    kind: str          # "image" | "video"
    key: str           # model key accepted in API requests
    label: str
    is_mock = False
    experimental = False
    supports_edit = False         # image: reference images (AI edit)
    supports_start_frame = False  # video: image -> video
    supports_audio = False
    supports_negative = False
    default_steps = 4
    default_frames = 49

    @abstractmethod
    def generate(self, ctx: JobContext) -> list[dict]:
        """Write output files into ctx.out_dir. Return a list of dicts:
        ``{"path": Path, "mime": str, "width": int, "height": int, ...}``."""

    def health(self) -> dict:
        return {"loaded": False}

    def unload(self) -> None:
        """Free accelerator memory. Called before another heavy model runs."""

    def describe(self) -> dict:
        return {
            "key": self.key, "label": self.label, "kind": self.kind, "mock": self.is_mock,
            "experimental": self.experimental,
            "supports": {"edit": self.supports_edit, "start_frame": self.supports_start_frame,
                         "audio": self.supports_audio, "negative_prompt": self.supports_negative},
            "default_steps": self.default_steps, **self.health(),
        }


class TorchProvider(Provider):
    """Shared device / dtype / memory handling for diffusers-based providers."""

    def __init__(self, model_id: str, cache_dir: str, device: str, dtype: str, low_memory: bool):
        self.model_id = model_id
        self.cache_dir = cache_dir
        self.device = device
        self.low_memory = low_memory
        self.dtype = self._pick_dtype(dtype)
        self.pipe = None

    def _pick_dtype(self, configured: str):
        import torch

        if configured == "float16":
            return torch.float16
        if configured == "bfloat16":
            return torch.bfloat16
        if configured == "float32":
            return torch.float32
        if self.device == "cuda":
            return torch.bfloat16
        if self.device == "mps":
            try:  # bf16 on MPS needs macOS 14+
                torch.ones(1, dtype=torch.bfloat16, device="mps")
                return torch.bfloat16
            except Exception:
                return torch.float16
        return torch.float32

    def _generator(self, seed: int):
        import torch

        # CPU generators give identical seeds across Mac/Windows/Linux.
        return torch.Generator(device="cuda" if self.device == "cuda" else "cpu").manual_seed(seed)

    def _place(self, pipe):
        if self.device == "cuda" and self.low_memory:
            pipe.enable_model_cpu_offload()
        else:
            pipe.to(self.device)
        for target in ("vae",):
            mod = getattr(pipe, target, None)
            if self.low_memory and mod is not None and hasattr(mod, "enable_tiling"):
                try:
                    mod.enable_tiling()
                except Exception:
                    pass
        if self.low_memory and hasattr(pipe, "enable_attention_slicing") and self.device != "cuda":
            try:
                pipe.enable_attention_slicing()
            except Exception:
                pass
        return pipe

    def _free_cache(self) -> None:
        gc.collect()
        try:
            import torch

            if self.device == "mps" and hasattr(torch, "mps"):
                torch.mps.empty_cache()
            elif self.device == "cuda":
                torch.cuda.empty_cache()
        except Exception:
            pass

    def unload(self) -> None:
        if self.pipe is not None:
            self.pipe = None
            for attr in ("t2v", "i2v"):
                if hasattr(self, attr):
                    setattr(self, attr, None)
            self._free_cache()

    def health(self) -> dict:
        return {"loaded": self.pipe is not None, "device": self.device,
                "dtype": str(self.dtype).replace("torch.", ""), "model_id": self.model_id}

    @staticmethod
    def step_callback(ctx: JobContext, total: int, base: float, span: float, label: str):
        def cb(_pipe, step, _timestep, kwargs):
            ctx.raise_if_cancelled()
            ctx.progress(base + span * (step + 1) / max(total, 1), f"{label} {step + 1}/{total}")
            return kwargs
        return cb
