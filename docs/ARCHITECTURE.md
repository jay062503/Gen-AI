# Architecture

```
Browser UI (static/, vanilla JS)           Enfactum OS / AI Agent
        │  cookie session                          │  Bearer token + X-Owner-Id
        └───────────────┬──────────────────────────┘
                 FastAPI  studio/api.py   (auth, validation, media serving, CSP)
                        │
                 JobManager studio/worker.py ── SQLite studio/storage.py (jobs, assets)
                  ├─ heavy lane (1 at a time) ── providers/  flux2 · ltx_video · ltx2 · mock
                  └─ light lane (2 at a time) ── media_tools: ffmpeg video edits, Pillow retouch
                        │
                  data/  media/<job>/{0.png,0.thumb.jpg} · uploads/ · studio.db · models/
```

## Modules
| File | Responsibility |
|---|---|
| `config.py` | Settings from `.env`; sets `PYTORCH_ENABLE_MPS_FALLBACK` before torch loads |
| `hardware.py` | Detects device/memory → tier (`demo`, `compact`, `standard`, `large`) without importing torch unless installed |
| `schemas.py` | Request models; edit operations are a typed, validated union (safe for an LLM to emit) |
| `policy.py` | Prompt blocklist, edit-specific blocklist, webhook host allow-list |
| `storage.py` | SQLite (WAL) behind one lock; idempotency keys; path-safe artifact lookup; retention |
| `worker.py` | Two-lane queue, progress throttling, cooperative cancel, restart recovery, webhooks |
| `media_tools.py` | ffmpeg wrapper (probe, encode, edit with progress/cancel), Pillow retouch |
| `providers/` | `Provider` contract (`generate(ctx) -> artifacts`); real and mock backends |

## Design decisions
* **One process, in-process queue.** Matches a single-GPU machine; no Redis/Celery to install. Jobs persist in SQLite and are re-queued after a crash.
* **Two lanes.** AI generation is minutes long; trimming a clip must not wait behind it.
* **One heavy model resident.** On Apple Silicon the GPU shares RAM, so the other model is unloaded before a job runs.
* **Providers are swappable.** Add a backend by subclassing `Provider` and registering it in `providers/registry.py` (e.g. an MLX backend for faster Apple Silicon inference, or a Wan/other video model).
* **Mock backend is a feature, not a stub.** It exercises the entire pipeline, so the UI, tests and Enfactum integration can be developed on any laptop.
* **Server-side media only.** Uploads become opaque `asset_id`s; the API never accepts file-system paths.
* **ffmpeg ships with `pip`.** `imageio-ffmpeg` bundles a static binary; a system ffmpeg is preferred when present (`FFMPEG_BIN` overrides).
* **Cancellation is cooperative:** diffusers step callbacks and ffmpeg progress lines check the flag, so cancel takes effect within one denoising step.

## Extending
* New edit op: add a model to `schemas.py` (`VideoOp`/`ImageOp`), handle it in `media_tools.build_video_filters` / `adjust_image`, add a control in `static/app.js`.
* New model: new provider + registry entry + `capabilities` automatically reports it to the UI.
