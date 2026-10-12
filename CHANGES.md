# What changed from the prototype (genai-media-studio-standalone)

The `.tar.gz` and `.zip` you sent were **byte-identical** — one project packaged twice. This release merges them into a single cross-platform project and turns the text-to-image/video API into a full studio.

## Defects found in the prototype (all fixed)
| # | Problem | Effect |
|---|---|---|
| 1 | `app.py` built `MediaWorker(store, img, vid, MAX_CONCURRENT, QUEUE_SIZE)` but the class accepted only 4 arguments | The service crashed at start-up with a `TypeError` |
| 2 | Auth defaulted to on, but the web page never sent a token | Every UI request would have failed (401/503) |
| 3 | `reference_paths` / `reference_image_path` accepted arbitrary **server file paths** | Local file read / path traversal. Now uploads become opaque asset ids |
| 4 | Reference images were accepted but the image provider ignored them; `negative_prompt` and `count` were also ignored; seeds were not recorded | Requests silently did less than asked |
| 5 | Identical requests were de-duplicated forever (hash of the payload) | With no seed, “Generate again” returned the old result. Now opt-in via `idempotency_key` |
| 6 | `owner_id` was a *query parameter* on job/media reads | Any caller could read anyone’s jobs. Now taken from the authenticated header |
| 7 | Queue size was hard-coded to 32 and “queue full” raised an unhandled `RuntimeError` | HTTP 500. Now configurable, returns 429 |
| 8 | DB reads outside the lock on a shared SQLite connection | Thread-safety race |
| 9 | Default video model was LTX-2.5 (22B + a 12B text encoder) | Cannot fit on a typical Mac mini. Default is now LTX-Video 2B; LTX-2.5 kept as an experimental option for big machines |
| 10 | `torch` imported at module load; `diffusers @ git+…` required git | Slow/crashing start without PyTorch; Windows installs needing git. Now lazy, with a light core install + optional AI runtime |
| 11 | Setup scripts hard-coded `python3.12` / `py -3.12` and a Windows-only venv name | Failed on Macs without that exact Python. Now auto-detects 3.10–3.13 |
| 12 | No progress, cancel, history, thumbnails, download, or player | — |

## Added
Web studio (4 modes, player, compare, history, drag-and-drop) · image **editing** (FLUX.2 klein references + Pillow quick-adjust) · video **editing** (ffmpeg: trim, speed, reverse, reframe, rotate, mirror, filters, colour, fades, mute, GIF, frame grab) · image→video · progress & cancel · restart recovery · light/heavy lanes · HEIC support · hardware tiers & doctor · demo mode · per-user scoping, idempotency, signed webhooks · security headers · 34 automated tests · Enfactum integration guide.

## Deliberately not included
* Natural-language video editing by an LLM (the validated `ops` schema is the hook for Enfactum AI to add it).
* AI video-to-video restyling — no verified, Mac-feasible model path was available to test; the provider interface is ready for one.
* Per-user accounts — the studio trusts Enfactum to authenticate users and pass `X-Owner-Id`.
