#!/usr/bin/env python3
"""Preflight check: tells you what will work on this machine, and what to fix."""
from __future__ import annotations

import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from studio import __version__, hardware  # noqa: E402
from studio.config import load_settings  # noqa: E402

OK, WARN, BAD = "  ok   ", " warn  ", " FAIL  "
problems = 0


def line(flag: str, label: str, detail: str = "") -> None:
    global problems
    problems += flag == BAD
    print(f"{flag}{label:<22}{detail}")


def main() -> int:
    s = load_settings()
    hw = hardware.detect()
    tier = hardware.tier(hw)
    print(f"\nMedia Studio {__version__} - system check\n")

    v = sys.version_info
    line(OK if (3, 10) <= v[:2] <= (3, 13) else BAD, "Python", f"{v.major}.{v.minor}.{v.micro}" + ("" if (3, 10) <= v[:2] <= (3, 13) else "  -> use 3.10-3.13"))
    line(OK, "Platform", f"{hw['os']} {hw['arch']}")
    line(OK, "Memory", f"{hw['ram_gb']} GB system RAM")

    try:
        from studio.media_tools import find_ffmpeg
        line(OK, "ffmpeg", find_ffmpeg())
    except RuntimeError as exc:
        line(BAD, "ffmpeg", str(exc))

    if hw["ml_installed"]:
        line(OK, "PyTorch", f"{hw['torch']}  diffusers {hw['diffusers']}")
        dev_ok = hw["device"] != "cpu"
        line(OK if dev_ok else WARN, "Accelerator", f"{hw['device_name']} ({hw['device']}), {hw['memory_gb']} GB"
             + ("" if dev_ok else "  -> no GPU found; AI models would be far too slow, using demo mode"))
    else:
        line(WARN, "PyTorch", "not installed -> demo mode.  Install AI runtime: python scripts/setup.py --ml")

    names = {"demo": "demo (placeholder media only)", "compact": "compact (16-24 GB: small sizes, short clips)",
             "standard": "standard (24-48 GB)", "large": "large (48 GB+)"}
    line(OK if tier != "demo" else WARN, "Hardware tier", names[tier])

    free = hardware.free_disk_gb(s.data_dir if s.data_dir.exists() else s.data_dir.parent)
    line(OK if free >= 50 else WARN, "Free disk", f"{free} GB" + ("" if free >= 50 else "  -> AI models need roughly 40-60 GB"))

    for label, repo in (("Image model cache", s.image_model_id), ("Video model cache", s.video_model_id)):
        cached = (s.model_cache / ("models--" + repo.replace("/", "--"))).exists()
        line(OK if cached else WARN, label, "downloaded" if cached else "not downloaded yet (first job downloads it, or run scripts/download_models.py)")

    with socket.socket() as sock:
        busy = sock.connect_ex(("127.0.0.1", s.port)) == 0
    line(WARN if busy else OK, f"Port {s.port}", "already in use -> set MEDIA_PORT in .env" if busy else "free")
    line(OK if s.api_token or s.is_loopback else BAD, "Access",
         "token set" if s.api_token else ("open on this computer only" if s.is_loopback else "network-exposed WITHOUT a token -> set MEDIA_API_TOKEN"))

    try:
        from studio.providers.registry import build_providers
        p = build_providers(s)
        line(OK, "Image backend", p["image"].label)
        line(OK, "Video backend", p["video"].label)
    except RuntimeError as exc:
        line(BAD, "Backends", str(exc))

    print("\nAll good." if not problems else f"\n{problems} problem(s) to fix before starting.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
