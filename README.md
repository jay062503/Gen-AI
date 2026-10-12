# Media Studio

A local, private **image and video studio**: generate from text, edit your own photos and videos, preview and download — all in one browser UI backed by one REST API. It runs on your own machine (a Mac mini is the primary target) and is designed to be plugged into **Enfactum AI / Enfactum OS** later.

| | |
|---|---|
| **Create image** | Text → image (FLUX.2 klein 4B), 1–4 variations, shape presets, seeds |
| **Create video** | Text → video, or start from an image (LTX-Video 2B), up to ~10 s |
| **Edit image** | Describe a change in words (AI, up to 4 reference images) + instant quick-adjust (brightness, crop, black & white, …) |
| **Edit video** | Trim, speed, reverse, reframe (16:9 / 9:16 / 1:1 …), rotate, mirror, filters, colour, fades, mute → MP4, GIF or single frame |
| **Play & download** | Built-in player (speed, loop, frame-step), Result/Original compare, one-click download, persistent history |
| **Bridges** | “Edit this image”, “Animate this image”, “Frame → edit”, “Frame → animate” move work between the tools |

One package runs everywhere: **macOS (Apple Silicon), Windows, Linux**. On a machine that can’t run the AI models it starts in **demo mode** (placeholder media) so you can still try and integrate the whole app.

---

## Quick start

### macOS (Mac mini) or Linux
```bash
unzip genai-media-studio.zip && cd genai-media-studio
bash start.sh
```
First run creates a private Python environment, installs dependencies (a few minutes; the AI runtime is large), then opens **http://127.0.0.1:8090**.
You can also double-click `start.command` in Finder (if macOS blocks it: right-click → Open, or run `xattr -dr com.apple.quarantine .` in the folder).

Need Python? `brew install python@3.12` (3.10–3.13 all work).

### Windows
Double-click **`start.bat`** (or run it in a terminal). Needs Python 3.10–3.13 from python.org with “Add to PATH” ticked.

### What happens on first run
| Machine | Result |
|---|---|
| Apple Silicon with ≥16 GB, or an NVIDIA GPU | Installs PyTorch + diffusers automatically → **AI mode**. Models download on first use (tens of GB), or run `python scripts/download_models.py` first. |
| Anything else (Intel laptop, small RAM, no GPU) | **Demo mode**. Everything works with clearly-labelled placeholder media. |

Force either: `bash start.sh --ml` / `bash start.sh --no-ml` (Windows: `start.bat --ml`).
Check your machine any time: `.venv/bin/python scripts/doctor.py` (Windows: `.venv\Scripts\python scripts\doctor.py`).

---

## Choosing hardware (starting points — see “Verification” below)

The image model needs about 13 GB, the video model more once its text encoder is counted. On Apple Silicon the GPU shares system memory with macOS.

| Mac mini memory | Tier | Suggested use |
|---|---|---|
| 16 GB | compact | Images ≤ 768 px; video 512×320, ~2 s. Tight — close other apps. Low-memory mode is on automatically. |
| 24 GB | standard | Images 1024 px; video 704×480, ~4 s. |
| 32 GB+ | standard | Same, with comfortable headroom. |
| 48–64 GB+ / CUDA 48 GB+ (e.g. HP ZGX Nano) | large | Longer clips; the experimental **LTX-2.5 22B** backend with sound (`MEDIA_VIDEO_BACKEND=ltx-2.5`, see `requirements-ltx25.txt`). |

Only one AI model is kept in memory at a time; the other is unloaded before it runs. Generation runs one job at a time; quick edits (trim, adjust …) run in a separate lane so they never wait behind a long render.

---

## Using the studio

* **Create image / video** – write a prompt, pick a shape, press *Generate* (Ctrl/Cmd + Enter). Progress, queue position and a *Cancel* button appear on the left; the result opens in the preview.
* **Edit image** – drop a photo (PNG, JPEG, WebP, iPhone HEIC), say what to change. Extra images act as references (“put the logo from image 2 on the mug”). Use *Result / Original* to compare. *Quick adjust* is instant and needs no AI.
* **Edit video** – drop a clip. Set *Playhead* to fill trim times from the player. Operations apply in a fixed order: trim → speed → reverse → resize → rotate → mirror → filter → colour → fades → mute. Save as MP4, GIF (≤ 20 s) or one frame.
* **History** (bottom strip) is stored on disk and survives restarts. Click to reopen, × to delete.
* Drag a file onto the preview: images go to *Edit image* (or the start frame in *Create video*), videos to *Edit video*.
* Keys: `←` `→` step one frame, `Space` play/pause.

---

## Configuration
Copy of `.env.example` is created as `.env` on first run. Highlights:

| Setting | Default | Meaning |
|---|---|---|
| `MEDIA_HOST` / `MEDIA_PORT` | `127.0.0.1` / `8090` | Use `0.0.0.0` to reach it from other devices — **a token is then mandatory** (the server refuses to start otherwise). |
| `MEDIA_API_TOKEN` | empty | Required for network access and for Enfactum calls. The browser UI asks for it once and keeps a cookie. |
| `MEDIA_IMAGE_BACKEND` / `MEDIA_VIDEO_BACKEND` | `auto` | `auto` picks real models when possible, else demo. Force `mock` for demo. |
| `MEDIA_LOW_MEMORY` | auto | `1` trades speed for memory. |
| `MEDIA_RETENTION_DAYS` | `0` | Auto-delete old items. |

Data lives in `./data` (SQLite database, uploads, results, model cache). Back it up or delete it freely.

---

## API in 30 seconds
Interactive docs: **http://127.0.0.1:8090/docs**

```bash
curl -s localhost:8090/v1/images/jobs -H 'Content-Type: application/json' \
     -d '{"prompt":"a red fox in snow, golden hour","count":2}'
# -> {"job_id":"i_…","status":"queued",…}   then poll:
curl -s localhost:8090/v1/jobs/i_…           # status, progress, artifacts[]
curl -O localhost:8090/v1/media/i_…/0?download=1
```
`POST /v1/assets` (upload) · `/v1/images/jobs` · `/v1/videos/jobs` · `/v1/images/adjust` · `/v1/videos/edit` · `GET /v1/jobs` · `/v1/jobs/{id}` · `POST /v1/jobs/{id}/cancel` · `DELETE /v1/jobs/{id}` · `/v1/media/{id}/{n}[/thumb]` · `/v1/capabilities` · `/health`.
Integration details (auth, per-user scoping, idempotency, signed webhooks, Laravel/Python examples): **`docs/ENFACTUM_INTEGRATION.md`**.

---

## Troubleshooting
| Symptom | Fix |
|---|---|
| “Python 3.10-3.13 was not found” | Install Python 3.12 (see above). |
| Status chip says **Demo mode** on a capable Mac | Run `scripts/doctor.py`; then `bash start.sh --ml`. Needs ≥ 16 GB memory. |
| Out-of-memory / macOS becomes sluggish | Smaller shape, shorter clip, `MEDIA_LOW_MEMORY=1`, quit other apps. |
| Model download asks for login | Accept the model terms on huggingface.co, run `.venv/bin/hf auth login` (or set `HF_TOKEN` in `.env`), retry. |
| Video won’t preview after upload | Some `.mov`/HEVC files can’t play in every browser; Safari plays them. Edit → export MP4 re-encodes to H.264. |
| Port in use | Change `MEDIA_PORT` in `.env`. |

## Tests
```bash
.venv/bin/python scripts/setup.py --dev && .venv/bin/python -m pytest
```

## Verification — what was and wasn’t tested
Tested automatically (34 tests) and by driving the real web page headlessly: the entire app in demo mode — queue, progress, cancel, restart recovery, uploads, HTTP Range playback, every edit operation (real ffmpeg), auth, per-user isolation, signed webhooks, path-traversal guard.
**Not testable where this package was prepared** (no GPU, no model access): real FLUX.2 / LTX inference. Those code paths were written against the verified public Diffusers 0.41 API (class names, call signatures and callbacks were checked), but the first real run on your Mac mini is the true test — `scripts/doctor.py` then a 512 px image is the quickest check. LTX-2.5 is marked experimental for the same reason. Visual layout was not inspected in a real browser.
