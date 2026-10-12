"""LTX-2.5 (22B, joint audio+video) - the provider from the original prototype, hardened.

EXPERIMENTAL: needs a large accelerator (CUDA 48 GB+ or 64 GB+ unified memory, e.g.
HP ZGX Nano) and, until it lands in a diffusers release, ``diffusers`` from git main
(see requirements-ltx25.txt). It was written against the public Diffusers API but
could not be run end-to-end on the machine used to prepare this package.
"""
from __future__ import annotations

import inspect

from ..media_tools import cover_resize, load_image
from .base import JobContext, TorchProvider, pick_seed


class LTX25Provider(TorchProvider):
    kind = "video"
    key = "ltx-2.5"
    label = "LTX-2.5 22B (audio + video)"
    experimental = True
    supports_start_frame = True
    supports_audio = True
    supports_negative = True
    default_steps = 8  # distilled sigma schedule

    def __init__(self, *a, default_size: tuple = (768, 512), default_frames: int = 121, **kw):
        super().__init__(*a, **kw)
        self.default_size = default_size
        self.default_frames = default_frames
        self.t2v = None
        self.i2v = None

    def _load(self, ctx: JobContext, need_i2v: bool) -> None:
        if self.t2v is None:
            from diffusers import LTX2Pipeline

            ctx.progress(0.02, "Loading LTX-2.5 (very large first download)")
            self.t2v = self._place(LTX2Pipeline.from_pretrained(self.model_id, torch_dtype=self.dtype,
                                                                cache_dir=self.cache_dir))
            self.pipe = self.t2v
        if need_i2v and self.i2v is None:
            from diffusers import LTX2ImageToVideoPipeline

            self.i2v = LTX2ImageToVideoPipeline.from_pipe(self.t2v)

    def generate(self, ctx: JobContext) -> list[dict]:
        from diffusers.pipelines.ltx2.utils import DEFAULT_NEGATIVE_PROMPT, DISTILLED_SIGMA_VALUES
        from diffusers.utils import encode_video

        p = ctx.payload
        ref_id = p.get("reference_asset_id")
        self._load(ctx, need_i2v=bool(ref_id))
        w, h = (p.get("width"), p.get("height")) if p.get("width") else self.default_size
        frames = int(p.get("frames") or self.default_frames)
        fps = float(p.get("fps", 24))
        seed = pick_seed(p)
        pipe = self.i2v if ref_id else self.t2v

        kwargs = dict(
            prompt=p["prompt"], negative_prompt=p.get("negative_prompt") or DEFAULT_NEGATIVE_PROMPT,
            width=w, height=h, num_frames=frames, frame_rate=fps, sigmas=DISTILLED_SIGMA_VALUES,
            guidance_scale=1.0, audio_guidance_scale=1.0, generator=self._generator(seed),
            output_type="np", return_dict=False,
        )
        if "callback_on_step_end" in inspect.signature(pipe.__call__).parameters:
            kwargs["callback_on_step_end"] = self.step_callback(ctx, len(DISTILLED_SIGMA_VALUES), 0.03, 0.85, "Step")
            kwargs["callback_on_step_end_tensor_inputs"] = []
        if ref_id:
            kwargs["image"] = cover_resize(load_image(ctx.asset_path(ref_id)), (w, h))

        video, audio = pipe(**kwargs)
        ctx.raise_if_cancelled()
        ctx.progress(0.9, "Encoding video")
        want_audio = bool(p.get("audio", True)) and audio is not None
        audio_t = audio[0].float().cpu() if want_audio else None
        out = ctx.out_dir / "0.mp4"
        encode_video(video[0], fps=int(round(fps)), output_path=str(out), audio=audio_t,
                     audio_sample_rate=pipe.vocoder.config.output_sampling_rate if want_audio else None)
        self._free_cache()
        return [{"path": out, "mime": "video/mp4", "width": w, "height": h, "duration": round(frames / fps, 3),
                 "fps": fps, "frames": frames, "seed": seed, "audio": want_audio}]
