"""Demo backends: no GPU, no downloads, instant start.

They exercise the complete pipeline (queue, progress, cancel, previews, edits,
downloads) so the app and any integration can be validated on any machine.
The outputs are clearly labelled placeholders, not AI generations.
"""
from __future__ import annotations

import hashlib
import os
import textwrap
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ..media_tools import cover_resize, encode_frames, fit_for_model, load_image
from .base import JobContext, Provider, pick_seed

DELAY = float(os.getenv("MEDIA_MOCK_DELAY", "0.15"))


def _palette(prompt: str, seed: int) -> tuple[tuple, tuple]:
    h = hashlib.sha256(f"{prompt}|{seed}".encode()).digest()
    a = (40 + h[0] % 140, 40 + h[1] % 140, 60 + h[2] % 160)
    b = (60 + h[3] % 180, 60 + h[4] % 160, 40 + h[5] % 140)
    return a, b


def _gradient(w: int, h: int, a: tuple, b: tuple) -> Image.Image:
    t = np.linspace(0, 1, h, dtype=np.float32)[:, None, None]
    arr = (np.array(a, np.float32) * (1 - t) + np.array(b, np.float32) * t)
    return Image.fromarray(np.repeat(arr, w, axis=1).astype(np.uint8), "RGB")


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def _caption(img: Image.Image, text: str, badge: str) -> None:
    d = ImageDraw.Draw(img, "RGBA")
    w, h = img.size
    f = _font(max(12, w // 28))
    lines = textwrap.wrap(text, width=max(18, w // (max(12, w // 28) // 2 + 3)))[:4]
    y = h - (len(lines) + 1) * (f.size + 6) - 10 if hasattr(f, "size") else h - 90
    d.rectangle([0, y - 8, w, h], fill=(0, 0, 0, 120))
    for ln in lines:
        d.text((16, y), ln, fill=(255, 255, 255, 235), font=f)
        y += (f.size if hasattr(f, "size") else 14) + 6
    d.text((16, 12), badge, fill=(255, 255, 255, 220), font=_font(max(12, w // 36)))


def _mock_size(payload: dict, refs: list, default, cap: int = 768) -> tuple[int, int]:
    w, h = payload.get("width"), payload.get("height")
    if not w or not h:
        if refs:
            side = default if isinstance(default, int) else max(default)
            w, h = fit_for_model(refs[0], side * side).size
        else:
            w, h = (default, default) if isinstance(default, int) else default
    scale = min(1.0, cap / max(w, h))
    return max(64, int(w * scale) // 2 * 2), max(64, int(h * scale) // 2 * 2)


class MockImageProvider(Provider):
    kind = "image"
    key = "demo-image"
    label = "Demo image (placeholder)"
    is_mock = True
    supports_edit = True
    default_steps = 4

    def generate(self, ctx: JobContext) -> list[dict]:
        p = ctx.payload
        refs = [load_image(ctx.asset_path(a)) for a in p.get("reference_asset_ids", [])]
        w, h = _mock_size(p, refs, 512)
        seed = pick_seed(p)
        steps, count = int(p.get("steps", 4)), int(p.get("count", 1))
        outs = []
        for i in range(count):
            a, b = _palette(p["prompt"], seed + i)
            if refs:
                img = cover_resize(refs[0], (w, h)).convert("RGB")
                tint = Image.new("RGB", (w, h), a)
                img = Image.blend(img, tint, 0.28)
            else:
                img = _gradient(w, h, a, b)
            for s in range(steps):
                ctx.raise_if_cancelled()
                ctx.progress((i + (s + 1) / steps) / count, f"Denoising {s + 1}/{steps}")
                time.sleep(DELAY)
            _caption(img, p["prompt"], "DEMO EDIT" if refs else "DEMO IMAGE")
            path = ctx.out_dir / f"{i}.png"
            img.save(path, "PNG")
            outs.append({"path": path, "mime": "image/png", "width": w, "height": h, "seed": seed + i})
        return outs


class MockVideoProvider(Provider):
    kind = "video"
    key = "demo-video"
    label = "Demo video (placeholder)"
    is_mock = True
    supports_start_frame = True
    supports_negative = True
    default_steps = 20

    def generate(self, ctx: JobContext) -> list[dict]:
        p = ctx.payload
        ref = load_image(ctx.asset_path(p["reference_asset_id"])) if p.get("reference_asset_id") else None
        w, h = _mock_size(p, [ref] if ref else [], (576, 320), cap=640)
        frames, fps = int(p["frames"]), float(p.get("fps", 24))
        seed = pick_seed(p)
        a, b = _palette(p["prompt"], seed)
        base = cover_resize(ref, (w, h)) if ref else _gradient(w, h, a, b)
        f = _font(max(12, w // 24))

        for s in range(8):  # simulated denoising so the progress UI has something to show
            ctx.raise_if_cancelled()
            ctx.progress(0.7 * (s + 1) / 8, f"Denoising {s + 1}/8")
            time.sleep(DELAY)

        def gen():
            for n in range(frames):
                ctx.raise_if_cancelled()
                img = base.copy()
                d = ImageDraw.Draw(img, "RGBA")
                t = n / max(frames - 1, 1)
                cx = int(w * (0.15 + 0.7 * t))
                cy = int(h * (0.5 + 0.18 * np.sin(t * 6.28)))
                r = max(10, h // 8)
                d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 255, 255, 190))
                d.rectangle([0, h - 6, int(w * t), h], fill=(255, 255, 255, 220))
                d.text((12, 10), f"DEMO VIDEO  {n + 1}/{frames}", fill=(255, 255, 255, 230), font=f)
                yield np.asarray(img.convert("RGB"))

        out = ctx.out_dir / "0.mp4"
        encode_frames(gen(), fps, out, size=(w, h), cancelled=ctx.is_cancelled)
        return [{"path": out, "mime": "video/mp4", "width": w - w % 2, "height": h - h % 2,
                 "duration": round(frames / fps, 3), "fps": fps, "frames": frames, "seed": seed}]
