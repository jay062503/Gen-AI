#!/usr/bin/env python3
"""Pre-download model weights into the studio's model cache (resumable).

    python scripts/download_models.py              # image + video (default models)
    python scripts/download_models.py --image
    python scripts/download_models.py --video
    python scripts/download_models.py --ltx25      # experimental 22B model (very large)

If Hugging Face asks you to accept a model's terms: open the model page in a browser,
accept them, then run `hf auth login` (or set HF_TOKEN in .env) and re-run this script.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from studio.config import load_settings  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", action="store_true")
    ap.add_argument("--video", action="store_true")
    ap.add_argument("--ltx25", action="store_true")
    args = ap.parse_args()
    s = load_settings()
    todo = []
    if args.image or not (args.image or args.video or args.ltx25):
        todo.append((s.image_model_id, None))
    if args.video or not (args.image or args.video or args.ltx25):
        todo.append((s.video_model_id, None))
    if args.ltx25:
        todo.append((s.ltx25_model_id, ["transformer_full/*"]))

    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print("huggingface_hub is missing. Run: python scripts/setup.py --ml")
        return 1

    s.model_cache.mkdir(parents=True, exist_ok=True)
    for repo, ignore in todo:
        print(f"\nDownloading {repo}  ->  {s.model_cache}")
        try:
            snapshot_download(repo_id=repo, cache_dir=str(s.model_cache), ignore_patterns=ignore)
        except Exception as exc:
            name = type(exc).__name__
            print(f"\n  Could not download {repo}: {name}: {exc}")
            if "401" in str(exc) or "403" in str(exc) or "Gated" in name:
                print("  This model needs you to accept its terms on huggingface.co and log in (see the top of this script).")
            return 1
    print("\nDone. Start the studio with start.sh / start.bat.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
