"""OpenVINO Stable Diffusion provider for Intel GPUs."""

from __future__ import annotations

import gc
from pathlib import Path

from ..media_tools import fit_for_model, load_image
from .base import JobContext, Provider, pick_seed


class OpenVINOSDProvider(Provider):
    kind = "image"
    key = "openvino-sd"
    label = "Stable Diffusion 1.5 INT8 · OpenVINO"
    supports_edit = True
    supports_negative = True
    default_steps = 10

    def __init__(
        self,
        model_id: str,
        cache_dir: str,
        device: str = "GPU",
        dtype: str = "float32",
        low_memory: bool = True,
        default_side: int = 512,
    ):
        self.model_id = model_id
        self.cache_dir = cache_dir
        self.device = device
        self.low_memory = low_memory
        self.default_side = min(int(default_side), 512)
        self.pipe = None
        self.edit_pipe = None

    def _load(self, ctx: JobContext) -> None:
        if self.pipe is not None:
            return

        if self.edit_pipe is not None:
            self.edit_pipe = None
            self._free_cache()

        from optimum.intel import OVStableDiffusionPipeline

        ctx.progress(
            0.02,
            "Loading Stable Diffusion 1.5 INT8 / OpenVINO..."
        )

        self.pipe = OVStableDiffusionPipeline.from_pretrained(
            self.model_id,
            cache_dir=self.cache_dir,
            compile=False,
            safety_checker=None,
        )

        ctx.progress(0.20, "Compiling OpenVINO model on Intel GPU...")

        # Keep the first test at 512x512, which is the sensible
        # resolution for this laptop/model combination.
        self.pipe.reshape(
            1,
            self.default_side,
            self.default_side,
        )

        self.pipe.compile()

        ctx.progress(0.30, "OpenVINO model ready")

    def _load_edit(self, ctx: JobContext) -> None:
        if self.edit_pipe is not None:
            return

        # Avoid keeping both full pipelines in memory on low-RAM machines.
        if self.pipe is not None:
            self.pipe = None
            self._free_cache()

        from optimum.intel import OVStableDiffusionImg2ImgPipeline

        ctx.progress(0.02, "Loading OpenVINO image-editing pipeline...")
        self.edit_pipe = OVStableDiffusionImg2ImgPipeline.from_pretrained(
            self.model_id,
            cache_dir=self.cache_dir,
            export=False,
            compile=False,
            safety_checker=None,
        )
        self.edit_pipe.reshape(1, self.default_side, self.default_side)
        self.edit_pipe.compile()
        ctx.progress(0.30, "OpenVINO editing pipeline ready")

    def generate(self, ctx: JobContext) -> list[dict]:
        p = ctx.payload
        refs = p.get("reference_asset_ids") or (
            [p["reference_asset_id"]] if p.get("reference_asset_id") else []
        )

        if refs:
            return self._edit(ctx, refs)

        self._load(ctx)

        ctx.raise_if_cancelled()

        width = int(p.get("width") or self.default_side)
        height = int(p.get("height") or self.default_side)

        # Keep dimensions reasonable for Intel Iris Xe.
        width = max(256, min(width, 512))
        height = max(256, min(height, 512))

        # SD models work best with dimensions divisible by 8.
        width = (width // 8) * 8
        height = (height // 8) * 8

        seed = pick_seed(p)
        count = max(1, int(p.get("count", 1)))
        steps = max(1, min(int(p.get("steps", self.default_steps)), 20))
        guidance = float(p.get("guidance_scale", 7.5))

        prompt = str(p["prompt"])
        negative_prompt = p.get("negative_prompt")

        outs = []

        for i in range(count):
            ctx.raise_if_cancelled()

            current_seed = (seed + i) % 2**32

            ctx.progress(
                0.30,
                f"Generating image {i + 1}/{count}..."
            )

            generator = None

            # OpenVINO's pipeline does not require a torch GPU generator.
            # Keep generation deterministic where supported by the
            # underlying pipeline.
            try:
                import torch

                generator = torch.Generator(device="cpu").manual_seed(
                    current_seed
                )
            except Exception:
                generator = None

            kwargs = {
                "prompt": prompt,
                "height": height,
                "width": width,
                "num_inference_steps": steps,
                "guidance_scale": guidance,
            }

            if negative_prompt:
                kwargs["negative_prompt"] = str(negative_prompt)

            if generator is not None:
                kwargs["generator"] = generator

            result = self.pipe(**kwargs)

            image = result.images[0]

            path = ctx.out_dir / f"{i}.png"
            image.save(path, "PNG")

            ctx.progress(
                0.95,
                f"Image {i + 1}/{count} complete"
            )

            outs.append(
                {
                    "path": path,
                    "mime": "image/png",
                    "width": image.width,
                    "height": image.height,
                    "seed": current_seed,
                }
            )

        self._free_cache()

        return outs

    def _edit(self, ctx: JobContext, refs: list[str]) -> list[dict]:
        if len(refs) != 1:
            raise ValueError(
                "OpenVINO Stable Diffusion editing supports one reference image at a time."
            )

        from PIL import Image

        self._load_edit(ctx)
        ctx.raise_if_cancelled()

        image = load_image(ctx.asset_path(refs[0])).convert("RGB")

        # Both pipelines are compiled for this fixed square resolution.
        width = height = self.default_side
        image = image.resize((width, height), Image.Resampling.LANCZOS)

        p = ctx.payload
        prompt = str(p["prompt"])
        negative_prompt = p.get("negative_prompt")
        seed = pick_seed(p)
        count = max(1, min(int(p.get("count", 1)), 2))
        steps = max(1, min(int(p.get("steps", self.default_steps)), 20))
        guidance = float(p.get("guidance_scale", 7.5))
        strength = max(0.05, min(float(p.get("strength", 0.65)), 1.0))
        outs = []

        for i in range(count):
            ctx.raise_if_cancelled()
            current_seed = (seed + i) % 2**32
            kwargs = {
                "prompt": prompt,
                "image": image,
                "strength": strength,
                "height": height,
                "width": width,
                "num_inference_steps": steps,
                "guidance_scale": guidance,
            }
            if negative_prompt:
                kwargs["negative_prompt"] = str(negative_prompt)

            try:
                import torch
                kwargs["generator"] = torch.Generator(device="cpu").manual_seed(current_seed)
            except Exception:
                pass

            ctx.progress(0.30, f"Editing image {i + 1}/{count}...")
            result = self.edit_pipe(**kwargs)
            output = result.images[0]
            out_path = ctx.out_dir / f"{i}.png"
            output.save(out_path, "PNG")
            outs.append({
                "path": out_path,
                "mime": "image/png",
                "width": output.width,
                "height": output.height,
                "seed": current_seed,
            })

        self._free_cache()
        return outs

    def _free_cache(self) -> None:
        gc.collect()

    def unload(self) -> None:
        self.pipe = None
        self.edit_pipe = None
        self._free_cache()

    def health(self) -> dict:
        return {
            "loaded": self.pipe is not None,
            "device": self.device,
            "model_id": self.model_id,
        }