"""Plumbing tests for the real model providers using stand-ins for torch/diffusers.

These do NOT test model quality or memory. They check that our code passes exactly the keyword
arguments the real Diffusers pipelines accept, reports progress, honours cancel, encodes video
and writes files - i.e. that the paths unreachable without a GPU contain no typos.
"""
import sys
import types

import numpy as np
import pytest
from PIL import Image

from conftest import png_bytes
from studio.media_tools import Cancel, probe
from studio.providers.base import JobContext

FLUX_KWARGS = {"image", "prompt", "height", "width", "num_inference_steps", "guidance_scale", "generator",
               "callback_on_step_end", "callback_on_step_end_tensor_inputs"}
LTX_KWARGS = {"prompt", "negative_prompt", "height", "width", "num_frames", "frame_rate", "num_inference_steps",
              "guidance_scale", "generator", "output_type", "callback_on_step_end", "callback_on_step_end_tensor_inputs"}


@pytest.fixture
def fake_ml(monkeypatch):
    torch = types.ModuleType("torch")
    torch.float16, torch.bfloat16, torch.float32 = "float16", "bfloat16", "float32"
    torch.ones = lambda *a, **k: None

    class Gen:
        def __init__(self, device="cpu"): self.device, self.seed = device, None
        def manual_seed(self, s): self.seed = s; return self
    torch.Generator = Gen
    torch.mps = types.SimpleNamespace(empty_cache=lambda: None)
    torch.cuda = types.SimpleNamespace(empty_cache=lambda: None, is_available=lambda: False)

    calls = {}

    class Base:
        vae = types.SimpleNamespace(enable_tiling=lambda: calls.setdefault("tiling", True))
        @classmethod
        def from_pretrained(cls, model_id, torch_dtype=None, cache_dir=None):
            calls["loaded"] = (cls.__name__, model_id); return cls()
        @classmethod
        def from_pipe(cls, pipe): return cls()
        def to(self, dev): calls["device"] = dev; return self
        def enable_attention_slicing(self): calls["slicing"] = True
        def _steps(self, kw, n):
            assert set(kw) <= self.ALLOWED, f"unexpected kwargs: {set(kw) - self.ALLOWED}"
            for i in range(n):
                kw["callback_on_step_end"](self, i, 0, {})

    class Flux2KleinPipeline(Base):
        ALLOWED = FLUX_KWARGS
        def __call__(self, **kw):
            self._steps(kw, kw["num_inference_steps"])
            calls.setdefault("flux", []).append(kw)
            return types.SimpleNamespace(images=[Image.new("RGB", (kw["width"], kw["height"]), "blue")])

    class LTXPipeline(Base):
        ALLOWED = LTX_KWARGS
        def __call__(self, **kw):
            self._steps(kw, kw["num_inference_steps"])
            calls.setdefault("ltx", []).append(kw)
            frames = np.random.rand(1, kw["num_frames"], kw["height"], kw["width"], 3).astype(np.float32)
            return types.SimpleNamespace(frames=frames)

    class LTXImageToVideoPipeline(LTXPipeline):
        ALLOWED = LTX_KWARGS | {"image"}

    diffusers = types.ModuleType("diffusers")
    diffusers.Flux2KleinPipeline, diffusers.LTXPipeline = Flux2KleinPipeline, LTXPipeline
    diffusers.LTXImageToVideoPipeline = LTXImageToVideoPipeline
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "diffusers", diffusers)
    return calls


def ctx(tmp_path, payload, cancel_after=None):
    img = tmp_path / "ref.png"
    img.write_bytes(png_bytes((640, 360)))
    seen = []
    n = {"c": 0}

    def is_cancelled():
        n["c"] += 1
        return cancel_after is not None and n["c"] > cancel_after

    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    return JobContext(job_id="t", payload=payload, out_dir=out, asset_path=lambda a: img,
                      report=lambda f, m="": seen.append((f, m)), is_cancelled=is_cancelled), seen


def test_flux_text_to_image_and_edit(fake_ml, tmp_path):
    from studio.providers.flux2 import Flux2KleinProvider
    p = Flux2KleinProvider("black-forest-labs/FLUX.2-klein-4B", cache_dir="c", device="mps", dtype="auto", low_memory=True)
    c, seen = ctx(tmp_path, {"prompt": "fox", "width": 512, "height": 512, "steps": 4, "count": 2, "seed": 5})
    arts = p.generate(c)
    assert [a["seed"] for a in arts] == [5, 6] and all(a["path"].exists() for a in arts)
    assert fake_ml["loaded"] == ("Flux2KleinPipeline", "black-forest-labs/FLUX.2-klein-4B") and fake_ml["device"] == "mps"
    assert "image" not in fake_ml["flux"][0] and seen and max(f for f, _ in seen) <= 1

    c, _ = ctx(tmp_path, {"prompt": "make it red", "steps": 4, "count": 1, "reference_asset_ids": ["a", "b"]})
    arts = p.generate(c)
    kw = fake_ml["flux"][-1]
    assert isinstance(kw["image"], list) and len(kw["image"]) == 2
    assert arts[0]["width"] % 16 == 0 and abs(arts[0]["width"] / arts[0]["height"] - 16 / 9) < 0.1
    p.unload()
    assert p.pipe is None


def test_flux_cancel(fake_ml, tmp_path):
    from studio.providers.flux2 import Flux2KleinProvider
    p = Flux2KleinProvider("m", cache_dir="c", device="cpu", dtype="auto", low_memory=False)
    c, _ = ctx(tmp_path, {"prompt": "x", "width": 256, "height": 256, "steps": 8, "count": 1}, cancel_after=2)
    with pytest.raises(Cancel):
        p.generate(c)


def test_ltx_video_text_and_image_to_video(fake_ml, tmp_path):
    from studio.providers.ltx_video import LTXVideoProvider
    p = LTXVideoProvider("Lightricks/LTX-Video", cache_dir="c", device="mps", dtype="auto", low_memory=True,
                         default_size=(320, 192), default_frames=17)
    c, seen = ctx(tmp_path, {"prompt": "waves", "frames": 17, "fps": 24, "steps": 3, "seed": 1})
    art = p.generate(c)[0]
    info = probe(art["path"])
    assert info["has_video"] and (info["width"], info["height"]) == (320, 192) and art["frames"] == 17
    assert "image" not in fake_ml["ltx"][0] and fake_ml["ltx"][0]["output_type"] == "np"

    c, _ = ctx(tmp_path, {"prompt": "waves", "frames": 9, "fps": 24, "steps": 2, "reference_asset_id": "a",
                          "width": 256, "height": 160})
    art = p.generate(c)[0]
    assert fake_ml["ltx"][-1]["image"].size == (256, 160)
    assert probe(art["path"])["has_video"]


def test_registry_selects_real_backends_when_ml_available(fake_ml, tmp_path, monkeypatch):
    from conftest import make_settings
    from studio import hardware
    from studio.providers import registry
    hw = {"os": "Darwin", "arch": "arm64", "python": "3.12", "ram_gb": 24, "torch": "x", "diffusers": "y",
          "device": "mps", "device_name": "m", "memory_gb": 24, "apple_silicon": True, "ml_installed": True}
    monkeypatch.setattr(hardware, "detect", lambda: hw)
    prov = registry.build_providers(make_settings(tmp_path, image_backend="auto", video_backend="auto"))
    assert (prov["image"].key, prov["video"].key) == ("flux2-klein-4b", "ltx-video")
    assert prov["video"].default_size == (704, 480) and prov["image"].low_memory is False
    hw.update(memory_gb=16, ram_gb=16)                              # compact tier: real models, small + low memory
    prov = registry.build_providers(make_settings(tmp_path, image_backend="auto", video_backend="auto"))
    assert not prov["image"].is_mock and prov["image"].low_memory is True and prov["video"].default_size == (512, 320)
    hw.update(memory_gb=8, ram_gb=8)                                # too small: demo mode
    prov = registry.build_providers(make_settings(tmp_path, image_backend="auto", video_backend="auto"))
    assert prov["image"].is_mock and prov["video"].is_mock


def test_forced_real_backend_without_ml_gives_clear_error(tmp_path, monkeypatch):
    from conftest import make_settings
    from studio import hardware
    from studio.providers import registry
    monkeypatch.setattr(hardware, "detect", lambda: {"ml_installed": False, "device": "cpu", "memory_gb": 4,
                                                      "ram_gb": 4, "apple_silicon": False})
    with pytest.raises(RuntimeError, match="setup.py --ml"):
        registry.build_providers(make_settings(tmp_path, image_backend="flux2"))
