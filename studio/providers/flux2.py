"""FLUX.2 [klein] 4B: one model for text->image AND instruction/reference editing.

With no reference images it is text-to-image; with 1-4 reference images the
prompt is an edit instruction ("make the jacket red", "put the logo from image 2
on the mug"). Apache-2.0 licensed 4B variant by default.
"""
from __future__ import annotations

from ..media_tools import fit_for_model, load_image
from .base import JobContext, TorchProvider, pick_seed

REF_MAX_PIXELS = 1024 * 1024


class Flux2KleinProvider(TorchProvider):
    kind = "image"
    key = "flux2-klein-4b"
    label = "FLUX.2 klein 4B"
    supports_edit = True
    default_steps = 4

    def __init__(self, *a, default_side: int = 1024, **kw):
        super().__init__(*a, **kw)
        self.default_side = default_side

    def _load(self, ctx: JobContext) -> None:
        if self.pipe is not None:
            return
        from diffusers import Flux2KleinPipeline

        ctx.progress(0.02, "Loading FLUX.2 klein (first run downloads the weights)")
        pipe = Flux2KleinPipeline.from_pretrained(self.model_id, torch_dtype=self.dtype, cache_dir=self.cache_dir)
        self.pipe = self._place(pipe)

    def generate(self, ctx: JobContext) -> list[dict]:
        p = ctx.payload
        self._load(ctx)
        refs = [fit_for_model(load_image(ctx.asset_path(a)), REF_MAX_PIXELS) for a in p.get("reference_asset_ids", [])]

        if p.get("width") and p.get("height"):
            w, h = int(p["width"]), int(p["height"])
        elif refs:  # match the first input's aspect ratio
            w, h = fit_for_model(refs[0], self.default_side**2).size
        else:
            w = h = self.default_side

        seed, count, steps = pick_seed(p), int(p.get("count", 1)), int(p.get("steps", 4))
        outs = []
        for i in range(count):
            ctx.raise_if_cancelled()
            kwargs = dict(
                prompt=p["prompt"], height=h, width=w, guidance_scale=1.0, num_inference_steps=steps,
                generator=self._generator((seed + i) % 2**32),
                callback_on_step_end=self.step_callback(ctx, steps, i / count, 0.95 / count,
                                                        f"Image {i + 1}/{count} · step" if count > 1 else "Step"),
                callback_on_step_end_tensor_inputs=[],
            )
            if refs:
                kwargs["image"] = refs if len(refs) > 1 else refs[0]
            image = self.pipe(**kwargs).images[0]
            path = ctx.out_dir / f"{i}.png"
            image.save(path, "PNG")
            outs.append({"path": path, "mime": "image/png", "width": image.width, "height": image.height,
                         "seed": (seed + i) % 2**32})
        self._free_cache()
        return outs
