"""LTX-Video (2B): text->video and image->video. Sized for 24 GB Apple Silicon / 12+ GB CUDA.

Output is encoded by our own ffmpeg step (H.264 + faststart) so it always plays in a browser.
"""
from __future__ import annotations

import numpy as np

from ..media_tools import cover_resize, encode_frames, load_image
from .base import JobContext, TorchProvider, pick_seed

DEFAULT_NEGATIVE = "worst quality, inconsistent motion, blurry, jittery, distorted, watermark"


class LTXVideoProvider(TorchProvider):
    kind = "video"
    key = "ltx-video"
    label = "LTX-Video 2B"
    supports_start_frame = True
    supports_negative = True
    default_steps = 30

    def __init__(self, *a, default_size: tuple = (704, 480), default_frames: int = 97, **kw):
        super().__init__(*a, **kw)
        self.default_size = default_size
        self.default_frames = default_frames
        self.t2v = None
        self.i2v = None

    def _load(self, ctx: JobContext, need_i2v: bool) -> None:
        if self.t2v is None:
            from diffusers import LTXPipeline

            ctx.progress(0.02, "Loading LTX-Video (first run downloads the weights)")
            self.t2v = self._place(LTXPipeline.from_pretrained(self.model_id, torch_dtype=self.dtype,
                                                               cache_dir=self.cache_dir))
            self.pipe = self.t2v
        if need_i2v and self.i2v is None:
            from diffusers import LTXImageToVideoPipeline

            self.i2v = LTXImageToVideoPipeline.from_pipe(self.t2v)  # shares weights, no extra memory

    def generate(self, ctx: JobContext) -> list[dict]:
        p = ctx.payload
        ref_id = p.get("reference_asset_id")
        self._load(ctx, need_i2v=bool(ref_id))
        w, h = (p.get("width"), p.get("height")) if p.get("width") else self.default_size
        frames = int(p.get("frames") or self.default_frames)
        fps = float(p.get("fps", 24))
        steps = int(p.get("steps") or self.default_steps)
        seed = pick_seed(p)

        kwargs = dict(
            prompt=p["prompt"], negative_prompt=p.get("negative_prompt") or DEFAULT_NEGATIVE,
            width=w, height=h, num_frames=frames, frame_rate=int(round(fps)),
            num_inference_steps=steps, guidance_scale=float(p.get("guidance_scale") or 3.0),
            generator=self._generator(seed), output_type="np",
            callback_on_step_end=self.step_callback(ctx, steps, 0.03, 0.82, "Step"),
            callback_on_step_end_tensor_inputs=[],
        )
        if ref_id:
            kwargs["image"] = cover_resize(load_image(ctx.asset_path(ref_id)), (w, h))
            pipe = self.i2v
        else:
            pipe = self.t2v

        result = pipe(**kwargs).frames[0]  # (F, H, W, 3) float in [0, 1]
        ctx.raise_if_cancelled()
        ctx.progress(0.88, "Encoding video")
        arr = (np.clip(np.asarray(result), 0, 1) * 255).astype(np.uint8)
        out = ctx.out_dir / "0.mp4"
        encode_frames(iter(arr), fps, out, size=(arr.shape[2], arr.shape[1]), cancelled=ctx.is_cancelled)
        self._free_cache()
        return [{"path": out, "mime": "video/mp4", "width": arr.shape[2] - arr.shape[2] % 2,
                 "height": arr.shape[1] - arr.shape[1] % 2, "duration": round(len(arr) / fps, 3),
                 "fps": fps, "frames": len(arr), "seed": seed}]
