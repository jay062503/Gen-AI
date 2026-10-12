"""Chooses which backend serves images and video on this machine."""
from __future__ import annotations

import logging

from .. import hardware
from ..config import Settings
from .base import Provider
from .mock import MockImageProvider, MockVideoProvider

log = logging.getLogger("studio.providers")


def build_providers(s: Settings) -> dict[str, Provider]:
    hw = hardware.detect()
    tier = hardware.tier(hw)
    limits = hardware.TIER_LIMITS[tier]
    real_ok = hw["ml_installed"] and tier != "demo"
    low = s.low_memory if s.low_memory is not None else limits["low_memory"]
    device = hardware.resolve_device(s.device)
    cache = str(s.model_cache)
    common = dict(cache_dir=cache, device=device, dtype=s.dtype, low_memory=low)

    img_choice = s.image_backend if s.image_backend != "auto" else ("flux2" if real_ok else "mock")
    vid_choice = s.video_backend if s.video_backend != "auto" else ("ltx-video" if real_ok else "mock")

    wants_real = img_choice != "mock" or vid_choice != "mock"
    if wants_real and not hw["ml_installed"]:
        raise RuntimeError(
            "A real model backend was requested but PyTorch/diffusers are not installed. "
            "Run `python scripts/setup.py --ml`, or set MEDIA_IMAGE_BACKEND=mock / MEDIA_VIDEO_BACKEND=mock.")

    if img_choice == "flux2":
        from .flux2 import Flux2KleinProvider

        image: Provider = Flux2KleinProvider(
            s.image_model_id,
            default_side=limits["image_default"],
            **common,
        )

    elif img_choice == "openvino-sd":
        from .openvino_sd import OpenVINOSDProvider

        image = OpenVINOSDProvider(
            s.image_model_id,
            cache_dir=cache,
            device="GPU",
            dtype=s.dtype,
            low_memory=True,
            default_side=512,
        )

    elif img_choice == "mock":
        image = MockImageProvider()

    else:
        raise RuntimeError(
            f"Unknown MEDIA_IMAGE_BACKEND '{img_choice}' "
            "(use auto, flux2, openvino-sd or mock)"
        )

    if vid_choice == "ltx-video":
        from .ltx_video import LTXVideoProvider

        video: Provider = LTXVideoProvider(s.video_model_id, default_size=limits["video_default"],
                                           default_frames=limits["video_frames"], **common)
    elif vid_choice == "ltx-2.5":
        from .ltx2 import LTX25Provider

        video = LTX25Provider(s.ltx25_model_id, default_frames=limits["video_frames"], **common)
    elif vid_choice == "mock":
        video = MockVideoProvider()
    else:
        raise RuntimeError(f"Unknown MEDIA_VIDEO_BACKEND '{vid_choice}' (use auto, ltx-video, ltx-2.5 or mock)")

    log.info("Tier=%s device=%s image=%s video=%s low_memory=%s", tier, device, image.key, video.key, low)
    return {"image": image, "video": video}
