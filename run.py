"""Start GenAI Media Studio.   python run.py [--open] [--host H] [--port P]"""
from __future__ import annotations

import argparse
import threading
import time
import webbrowser

from studio import __version__, hardware
from studio.config import load_settings


def main() -> None:
    ap = argparse.ArgumentParser(description="GenAI Media Studio")
    ap.add_argument("--host")
    ap.add_argument("--port", type=int)
    ap.add_argument("--open", action="store_true", help="open the browser when ready")
    args = ap.parse_args()

    s = load_settings()
    if args.host:
        s = s.with_(host=args.host)
    if args.port:
        s = s.with_(port=args.port)

    import uvicorn

    from studio.api import create_app

    try:
        app = create_app(s)
    except RuntimeError as exc:
        raise SystemExit(f"\n  Cannot start: {exc}\n")

    hw = hardware.detect()
    mode = "DEMO (placeholder media - run scripts/setup.py --ml for AI models)" if any(
        p.is_mock for p in app.state.manager.providers.values()) else "AI models"
    url = f"http://{'127.0.0.1' if s.host in ('0.0.0.0', '::') else s.host}:{s.port}"
    print(f"\n  GenAI Media Studio {__version__}")
    print(f"  Device : {hw['device_name']}  ({hw['device']}, tier: {hardware.tier(hw)})")
    print(f"  Mode   : {mode}")
    print(f"  Auth   : {'token required' if s.api_token else 'open (localhost only)'}")
    print(f"  Open   : {url}\n")

    if args.open:
        threading.Thread(target=lambda: (time.sleep(1.5), webbrowser.open(url)), daemon=True).start()
    uvicorn.run(app, host=s.host, port=s.port, log_level="info")


if __name__ == "__main__":
    main()
