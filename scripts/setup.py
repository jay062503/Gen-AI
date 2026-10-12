#!/usr/bin/env python3
"""Idempotent installer, run inside the project's virtualenv by start.sh / start.bat.

    python scripts/setup.py              # install what this machine can use (default)
    python scripts/setup.py --ml         # force the AI model runtime (PyTorch, diffusers ...)
    python scripts/setup.py --no-ml      # demo mode only (fast, tiny)
    python scripts/setup.py --reinstall  # ignore the "already installed" marker
    python scripts/setup.py --dev        # also install test tools
"""
from __future__ import annotations

import argparse
import hashlib
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKER = ROOT / ".venv" / ".studio-installed"
REQS = ["requirements.txt", "requirements-ml.txt"]


def run(*cmd: str) -> None:
    print("  $", " ".join(cmd), flush=True)
    subprocess.check_call(cmd, cwd=ROOT)


def pip(*args: str) -> None:
    run(sys.executable, "-m", "pip", "install", "--disable-pip-version-check", *args)


def ram_gb() -> float:
    try:
        import psutil

        return psutil.virtual_memory().total / 1024**3
    except Exception:
        return 0.0


def fingerprint(ml: bool) -> str:
    h = hashlib.sha256(b"ml" if ml else b"core")
    for name in REQS:
        h.update((ROOT / name).read_bytes())
    return h.hexdigest()[:16]


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--ml", action="store_true")
    g.add_argument("--no-ml", action="store_true")
    ap.add_argument("--reinstall", action="store_true")
    ap.add_argument("--dev", action="store_true")
    args = ap.parse_args()

    if not ((3, 10) <= sys.version_info[:2] <= (3, 13)):
        print(f"Python {platform.python_version()} is not supported. Use Python 3.10 - 3.13 (3.12 recommended).")
        return 1

    apple = platform.system() == "Darwin" and platform.machine().lower() in {"arm64", "aarch64"}
    nvidia = shutil.which("nvidia-smi") is not None
    ram = ram_gb()

    if args.ml:
        ml, why = True, "requested with --ml"
    elif args.no_ml:
        ml, why = False, "requested with --no-ml"
    elif (apple or nvidia) and (ram >= 15 or nvidia):
        ml, why = True, "Apple Silicon GPU" if apple else "NVIDIA GPU detected"
    else:
        ml, why = False, ("not enough memory for the AI models (needs ~16 GB+)" if apple else "no supported GPU detected")

    # An existing ML install is never downgraded implicitly.
    if MARKER.exists() and MARKER.read_text().startswith("ml") and not args.no_ml:
        ml = True

    stamp = ("ml:" if ml else "core:") + fingerprint(ml)
    if MARKER.exists() and MARKER.read_text().strip() == stamp and not args.reinstall and not args.dev:
        return 0

    print("\n== Media Studio setup ==")
    print(f"  Python {platform.python_version()} on {platform.system()} {platform.machine()}, {ram:.0f} GB RAM")
    print(f"  AI runtime: {'YES' if ml else 'no (demo mode)'} - {why}\n")

    pip("--upgrade", "pip", "setuptools", "wheel")
    pip("-r", "requirements.txt")
    if args.dev:
        pip("-r", "requirements-dev.txt")

    if ml:
        if platform.system() == "Windows" and nvidia:
            # PyPI's Windows torch wheel is CPU-only; NVIDIA users need the CUDA build.
            try:
                pip("torch>=2.7", "--index-url", "https://download.pytorch.org/whl/cu128")
            except subprocess.CalledProcessError:
                print("  (CUDA build of PyTorch unavailable - falling back to the default wheel)")
        pip("-r", "requirements-ml.txt")

    env, example = ROOT / ".env", ROOT / ".env.example"
    if not env.exists():
        shutil.copy(example, env)
        print("  Created .env (edit it to change settings)")

    MARKER.parent.mkdir(exist_ok=True)
    MARKER.write_text(stamp)
    print("\n  Setup complete.", end=" ")
    print("Run  python scripts/download_models.py  to fetch the AI models now (otherwise the first job downloads them)."
          if ml else "Running in demo mode. For real AI models on a capable machine: python scripts/setup.py --ml")
    return 0


if __name__ == "__main__":
    sys.exit(main())
