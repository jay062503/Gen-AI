"""ffmpeg + Pillow helpers: probing, thumbnails, encoding, video edits, image retouch.

ffmpeg is taken from $FFMPEG_BIN, then PATH, then the binary bundled with the
``imageio-ffmpeg`` wheel - so a plain ``pip install`` works on macOS, Windows
and Linux with nothing else to install.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import threading
from functools import lru_cache
from pathlib import Path
from typing import Callable, Iterable

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

try:  # iPhone / macOS photos
    import pillow_heif

    pillow_heif.register_heif_opener()
except Exception:  # pragma: no cover
    pass

Image.MAX_IMAGE_PIXELS = 80_000_000

Progress = Callable[[float], None]
Cancelled = Callable[[], bool]


class Cancel(Exception):
    """Raised when a running job is cancelled by the user."""


# ------------------------------------------------------------------ ffmpeg core
@lru_cache(maxsize=1)
def find_ffmpeg() -> str:
    env = os.getenv("FFMPEG_BIN")
    if env and Path(env).exists():
        return env
    which = shutil.which("ffmpeg")
    if which:
        return which
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:
        raise RuntimeError(
            "ffmpeg not found. Run `pip install imageio-ffmpeg` or install ffmpeg "
            "(macOS: `brew install ffmpeg`)."
        ) from exc


def _popen(args: list[str], **kw) -> subprocess.Popen:
    flags = {"creationflags": 0x08000000} if os.name == "nt" else {}  # no console window on Windows
    return subprocess.Popen([find_ffmpeg(), "-hide_banner", "-nostdin", *args], **flags, **kw)


def run_ffmpeg(args: list[str], *, duration: float | None = None,
               progress: Progress | None = None, cancelled: Cancelled | None = None) -> None:
    """Run ffmpeg, reporting progress (0-1) and honouring cancellation."""
    err = tempfile.TemporaryFile()
    proc = _popen(["-y", "-loglevel", "error", "-progress", "pipe:1", *args],
                  stdout=subprocess.PIPE, stderr=err, text=True)
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            if cancelled and cancelled():
                proc.kill()
                raise Cancel()
            if progress and duration and line.startswith(("out_time_us=", "out_time_ms=")):
                try:
                    secs = int(line.split("=", 1)[1]) / 1_000_000
                    progress(max(0.0, min(0.99, secs / duration)))
                except ValueError:
                    pass
        proc.wait()
    finally:
        if proc.poll() is None:
            proc.kill()
    if proc.returncode != 0:
        err.seek(0)
        msg = err.read().decode("utf-8", "replace").strip().splitlines()
        raise RuntimeError("ffmpeg failed: " + (msg[-1] if msg else f"exit {proc.returncode}"))


_DUR = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_VID = re.compile(r"Video:.*?(?<![0-9a-fA-Fx])(\d{2,5})x(\d{2,5})(?=[\s,\[])")
_FPS = re.compile(r"(\d+(?:\.\d+)?)\s*fps")
_ROT = re.compile(r"rotation of (-?\d+(?:\.\d+)?)")


def probe(path: Path | str) -> dict:
    """Return width/height/duration/fps/has_audio/has_video for a media file."""
    proc = subprocess.run([find_ffmpeg(), "-hide_banner", "-nostdin", "-i", str(path)],
                          capture_output=True, text=True,
                          **({"creationflags": 0x08000000} if os.name == "nt" else {}))
    text = proc.stderr
    info = {"has_video": False, "has_audio": "Audio:" in text, "width": None, "height": None,
            "duration": None, "fps": None}
    if m := _DUR.search(text):
        h, mi, s = m.groups()
        info["duration"] = round(int(h) * 3600 + int(mi) * 60 + float(s), 3)
    vline = next((ln for ln in text.splitlines() if "Video:" in ln and "attached pic" not in ln), None)
    if vline and (m := _VID.search(vline)):
        info["has_video"] = True
        w, h = int(m.group(1)), int(m.group(2))
        if (r := _ROT.search(text)) and abs(int(float(r.group(1)))) in (90, 270):
            w, h = h, w
        info["width"], info["height"] = w, h
        if f := _FPS.search(vline):
            info["fps"] = float(f.group(1))
    return info


def video_thumbnail(src: Path, dest: Path, at: float = 0.0, width: int = 480) -> None:
    for t in (at, 0.0):
        try:
            run_ffmpeg(["-ss", f"{t:.3f}", "-i", str(src), "-frames:v", "1",
                        "-vf", f"scale='min({width},iw)':-2", "-q:v", "4", str(dest)])
            if dest.exists() and dest.stat().st_size > 0:
                return
        except RuntimeError:
            continue
    raise RuntimeError("could not create video thumbnail")


def encode_frames(frames: Iterable[np.ndarray], fps: float, out: Path, *,
                  size: tuple[int, int], cancelled: Cancelled | None = None) -> int:
    """Encode uint8 RGB frames (H, W, 3) to browser-friendly H.264 MP4. Returns frame count."""
    w, h = size
    w, h = w - w % 2, h - h % 2
    proc = _popen(["-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                   "-s", f"{w}x{h}", "-r", f"{fps}", "-i", "-",
                   "-vf", "format=yuv420p", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                   "-movflags", "+faststart", str(out)],
                  stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    n = 0
    try:
        assert proc.stdin is not None
        for fr in frames:
            if cancelled and cancelled():
                proc.kill()
                raise Cancel()
            fr = np.ascontiguousarray(fr[:h, :w])
            proc.stdin.write(fr.tobytes())
            n += 1
        proc.stdin.close()
        err = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
        proc.wait()
    finally:
        if proc.poll() is None:
            proc.kill()
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg encode failed: " + (err.strip().splitlines() or ["?"])[-1])
    return n


# ------------------------------------------------------------------ video edits
_FILTERS = {
    "grayscale": "hue=s=0",
    "sepia": "colorchannelmixer=.393:.769:.189:0:.349:.686:.168:0:.272:.534:.131",
    "vivid": "eq=saturation=1.45:contrast=1.08",
    "warm": "colorchannelmixer=rr=1.08:bb=0.92",
    "cool": "colorchannelmixer=rr=0.92:bb=1.08",
    "sharpen": "unsharp=5:5:1.0:5:5:0.0",
    "blur": "gblur=sigma=2.5",
    "negative": "negate",
}


def _atempo(f: float) -> list[str]:
    chain = []
    while f > 2.0:
        chain.append("atempo=2.0")
        f /= 2.0
    while f < 0.5:
        chain.append("atempo=0.5")
        f /= 0.5
    chain.append(f"atempo={f:.4f}")
    return chain


def build_video_filters(ops: list, info: dict) -> tuple[list[str], list[str], bool, float | None]:
    """Translate typed ops into ffmpeg video/audio filter chains.

    Returns (video_filters, audio_filters, mute, output_duration).
    """
    vf: list[str] = []
    af: list[str] = []
    mute = False
    dur = info.get("duration")
    for op in ops:
        k = op.op
        if k == "trim":
            end = f":end={op.end}" if op.end else ""
            if op.end and op.end <= op.start:
                raise ValueError("trim end must be after start")
            vf.append(f"trim=start={op.start}{end},setpts=PTS-STARTPTS")
            af.append(f"atrim=start={op.start}{end},asetpts=PTS-STARTPTS")
            if dur is not None:
                dur = max(0.0, min(op.end or dur, dur) - op.start)
        elif k == "speed":
            vf.append(f"setpts=PTS/{op.factor}")
            af.extend(_atempo(op.factor))
            if dur is not None:
                dur /= op.factor
        elif k == "reverse":
            if (dur or 0) > 60:
                raise ValueError("reverse needs the whole clip in memory; trim to 60s or less first")
            vf.append("reverse")
            af.append("areverse")
        elif k == "resize":
            w, h = op.width - op.width % 2, op.height - op.height % 2
            if op.mode == "fit":
                vf.append(f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                          f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black")
            elif op.mode == "fill":
                vf.append(f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}")
            else:
                vf.append(f"scale={w}:{h}")
            vf.append("setsar=1")
        elif k == "rotate":
            vf.append({90: "transpose=1", 270: "transpose=2", 180: "hflip,vflip"}[op.degrees])
        elif k == "flip":
            vf.append("hflip" if op.axis == "h" else "vflip")
        elif k == "filter":
            vf.append(_FILTERS[op.name])
        elif k == "adjust":
            vf.append(f"eq=brightness={op.brightness:.3f}:contrast={op.contrast:.3f}:saturation={op.saturation:.3f}")
        elif k == "fade":
            if op.fade_in:
                vf.append(f"fade=t=in:st=0:d={op.fade_in}")
                af.append(f"afade=t=in:st=0:d={op.fade_in}")
            if op.fade_out:
                if dur is None:
                    raise ValueError("cannot fade out: clip duration is unknown")
                st = max(0.0, dur - op.fade_out)
                vf.append(f"fade=t=out:st={st:.3f}:d={op.fade_out}")
                af.append(f"afade=t=out:st={st:.3f}:d={op.fade_out}")
        elif k == "mute":
            mute = True
    return vf, af, mute, dur


def edit_video(src: Path, out: Path, ops: list, *, output: str = "mp4", frame_time: float = 0.0,
               progress: Progress | None = None, cancelled: Cancelled | None = None) -> dict:
    info = probe(src)
    if not info["has_video"]:
        raise ValueError("source has no video stream")
    even = "scale=trunc(iw/2)*2:trunc(ih/2)*2"

    if output == "frame":
        run_ffmpeg(["-ss", f"{frame_time:.3f}", "-i", str(src), "-frames:v", "1", str(out)])
        if not out.exists():
            raise ValueError("no frame at that time (past the end of the clip?)")
        w, h = Image.open(out).size
        return {"width": w, "height": h, "format": "png"}

    vf, af, mute, dur = build_video_filters(ops, info)
    vf.append(even)
    has_audio = info["has_audio"] and not mute
    if output == "gif":
        if (dur or 0) > 20:
            raise ValueError("GIF output is limited to 20 seconds - trim first")
        graph = ",".join(vf) + ",fps=15,scale='min(480,iw)':-2:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse"
        run_ffmpeg(["-i", str(src), "-vf", graph, "-an", "-loop", "0", str(out)],
                   duration=dur or info["duration"], progress=progress, cancelled=cancelled)
    else:
        vf.append("format=yuv420p")
        args = ["-i", str(src), "-vf", ",".join(vf)]
        if has_audio and af:
            args += ["-af", ",".join(af)]
        args += ["-c:v", "libx264", "-preset", "medium", "-crf", "20"]
        args += ["-c:a", "aac", "-b:a", "160k"] if has_audio else ["-an"]
        args += ["-movflags", "+faststart", str(out)]
        run_ffmpeg(args, duration=dur or info["duration"], progress=progress, cancelled=cancelled)
    res = probe(out)
    return {"width": res["width"], "height": res["height"], "duration": res["duration"],
            "fps": res["fps"], "format": output}


# ------------------------------------------------------------------ images
def load_image(path: Path | str, mode: str = "RGB") -> Image.Image:
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)
    if mode == "RGB" and img.mode in ("RGBA", "LA", "P"):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        rgba = img.convert("RGBA")
        bg.paste(rgba, mask=rgba.split()[-1])
        return bg
    return img.convert(mode)


def image_thumbnail(src: Path, dest: Path, size: int = 360) -> None:
    img = load_image(src)
    img.thumbnail((size, size), Image.LANCZOS)
    img.save(dest, "JPEG", quality=84)


def adjust_image(src: Path, out: Path, ops: list, fmt: str = "png") -> dict:
    img = load_image(src)
    for op in ops:
        k = op.op
        if k == "rotate":
            img = img.transpose({90: Image.Transpose.ROTATE_270, 180: Image.Transpose.ROTATE_180,
                                 270: Image.Transpose.ROTATE_90}[op.degrees])
        elif k == "flip":
            img = ImageOps.mirror(img) if op.axis == "h" else ImageOps.flip(img)
        elif k == "grayscale":
            img = ImageOps.grayscale(img).convert("RGB")
        elif k == "adjust":
            for enh, val in ((ImageEnhance.Brightness, op.brightness), (ImageEnhance.Contrast, op.contrast),
                             (ImageEnhance.Color, op.saturation), (ImageEnhance.Sharpness, op.sharpness)):
                if abs(val - 1) > 1e-3:
                    img = enh(img).enhance(val)
        elif k == "blur":
            img = img.filter(ImageFilter.GaussianBlur(op.radius))
        elif k == "resize":
            size = (op.width, op.height)
            if op.mode == "fit":
                img = ImageOps.contain(img, size, Image.LANCZOS)
            elif op.mode == "fill":
                img = ImageOps.fit(img, size, Image.LANCZOS)
            else:
                img = img.resize(size, Image.LANCZOS)
        elif k == "crop_aspect":
            target = op.ratio_w / op.ratio_h
            w, h = img.size
            if w / h > target:
                nw = round(h * target)
                img = img.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
            else:
                nh = round(w / target)
                img = img.crop((0, (h - nh) // 2, w, (h - nh) // 2 + nh))
    pil_fmt = {"png": "PNG", "jpeg": "JPEG", "webp": "WEBP"}[fmt]
    kw = {"optimize": True} if fmt == "png" else {"quality": 92}
    img.save(out, pil_fmt, **kw)
    return {"width": img.width, "height": img.height, "format": fmt}


def fit_for_model(img: Image.Image, max_pixels: int, multiple: int = 16) -> Image.Image:
    """Downscale to <= max_pixels and snap both sides to a multiple (model requirement)."""
    w, h = img.size
    scale = min(1.0, (max_pixels / (w * h)) ** 0.5)
    nw = max(multiple, int(w * scale) // multiple * multiple)
    nh = max(multiple, int(h * scale) // multiple * multiple)
    return img if (nw, nh) == (w, h) else img.resize((nw, nh), Image.LANCZOS)


def cover_resize(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    return ImageOps.fit(img, size, Image.LANCZOS)
