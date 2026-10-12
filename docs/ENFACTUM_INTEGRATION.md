# Integrating with Enfactum AI / Enfactum OS

Media Studio is a **standalone service with a REST API**. Enfactum never imports its code: it calls HTTP endpoints, so the studio can live on the Mac mini today and move to the HP ZGX Nano (CUDA) later by changing one URL.

```
 Enfactum OS (Laravel)  ─┐                     ┌─ Media Studio :8090
 Enfactum AI Agent (Py) ─┼─ HTTPS + token ───► │   queue → FLUX.2 / LTX-Video / ffmpeg
 Browser (optional UI) ──┘  X-Owner-Id: <user> └─ data/ (SQLite, uploads, results)
```

## 1. Switch it on for network access
In `.env` on the studio machine:
```
MEDIA_HOST=0.0.0.0
MEDIA_API_TOKEN=<64+ random characters>        # python -c "import secrets;print(secrets.token_urlsafe(48))"
MEDIA_CALLBACK_HOSTS=os.enfactum.internal      # only if you want webhooks
```
The server refuses to start on a non-loopback address without a token. Keep it on the intranet/VPN; put TLS in front (nginx/Caddy) if traffic leaves one host.

## 2. Authentication & per-user scoping
Send the token on every call: `Authorization: Bearer <token>` (or `X-Media-Token`).
Add **`X-Owner-Id: <enfactum user id>`** so each employee only sees their own jobs and uploads (history, media and assets are all scoped; another owner gets 404). Without the header, calls belong to the shared `local` owner. The header is honoured only with a valid token.

## 3. Typical flow
1. *(optional)* `POST /v1/assets` (multipart `file`) → `asset_id` for photos/videos to edit.
2. `POST /v1/images/jobs` · `/v1/videos/jobs` · `/v1/images/adjust` · `/v1/videos/edit` → `202` with `job_id`.
   Add `"idempotency_key": "<your id>"` so a retried request returns the same job instead of generating twice.
3. Wait: poll `GET /v1/jobs/{id}` (`status`: queued → running → succeeded | failed | cancelled, plus `progress` 0–1 and `message`) **or** pass `"callback_url"` for a webhook.
4. Fetch results **server-side**: `GET /v1/media/{id}/{index}` (supports Range; `?download=1` for attachment) and stream them to your user, so the studio token never reaches a browser.

Use `GET /v1/capabilities` to discover the active models, limits, supported features and whether the server is in demo mode.

### Webhook
`POST` to your `callback_url` with `{"event": "job.succeeded" | "job.failed" | "job.cancelled", "job": {…}}` and header `X-Media-Signature: sha256=<hex HMAC-SHA256 of the raw body, key = the API token>`. Delivery is retried 3 times. The URL’s host must be listed in `MEDIA_CALLBACK_HOSTS` (SSRF guard).

## 4. Laravel (Enfactum OS) example
```php
// config/services.php
'media' => ['url' => env('MEDIA_STUDIO_URL', 'http://127.0.0.1:8090'), 'token' => env('MEDIA_STUDIO_TOKEN')],

// app/Services/MediaStudio.php
use Illuminate\Support\Facades\Http;

class MediaStudio
{
    private function http(int $userId)
    {
        return Http::baseUrl(config('services.media.url'))
            ->withToken(config('services.media.token'))
            ->withHeaders(['X-Owner-Id' => "enfactum:{$userId}"])
            ->timeout(30);
    }

    public function image(int $userId, string $prompt, string $key): array
    {
        return $this->http($userId)->post('/v1/images/jobs', [
            'prompt' => $prompt, 'idempotency_key' => $key,
            'callback_url' => route('media.webhook'),          // optional
        ])->throw()->json();
    }

    public function job(int $userId, string $id): array
    {
        return $this->http($userId)->get("/v1/jobs/{$id}")->throw()->json();
    }

    public function stream(int $userId, string $id, int $i = 0)
    {
        return $this->http($userId)->withOptions(['stream' => true])->get("/v1/media/{$id}/{$i}");
    }
}

// Webhook controller — verify before trusting
$expected = 'sha256=' . hash_hmac('sha256', $request->getContent(), config('services.media.token'));
abort_unless(hash_equals($expected, (string) $request->header('X-Media-Signature')), 401);
```
Sanctum/RBAC: gate your own Laravel routes with a permission (e.g. `media.generate`) and map the Enfactum user id to `X-Owner-Id`; the studio itself has no user database.

## 5. Python (Enfactum AI Agent) example — as an agent tool
```python
import httpx, time, os

BASE, TOKEN = os.getenv("MEDIA_STUDIO_URL", "http://127.0.0.1:8090"), os.getenv("MEDIA_STUDIO_TOKEN", "")
H = {"Authorization": f"Bearer {TOKEN}", "X-Owner-Id": "agent"} if TOKEN else {}

def generate_image(prompt: str, timeout: int = 600) -> bytes:
    """Tool for the agent: returns PNG bytes."""
    with httpx.Client(base_url=BASE, headers=H, timeout=30) as c:
        job = c.post("/v1/images/jobs", json={"prompt": prompt}).raise_for_status().json()
        end = time.time() + timeout
        while time.time() < end:
            j = c.get(f"/v1/jobs/{job['job_id']}").json()
            if j["status"] == "succeeded":
                return c.get(j["artifacts"][0]["url"]).content
            if j["status"] in ("failed", "cancelled"):
                raise RuntimeError(j.get("error") or j["status"])
            time.sleep(1.5)
    raise TimeoutError("image job timed out")
```
Natural-language video editing (“cut the first 3 seconds and make it black & white”) is a good agent feature: have the LLM emit the `ops` list for `POST /v1/videos/edit` (schema in `/docs`) — the service validates every operation.

## 6. Moving to the HP ZGX Nano / a CUDA server
Copy the folder (or `git clone`) and run `bash start.sh --no-open --ml`. **Check that PyTorch actually sees the GPU**: `.venv/bin/python scripts/doctor.py` must report a `cuda` accelerator. On x86-64 Linux the default PyTorch wheel includes CUDA; on **ARM64 machines (check whether your ZGX Nano is one) the default wheel may be CPU-only** — install the CUDA build for your platform from pytorch.org / NVIDIA’s instructions first, then run `start.sh --ml`. If the machine has large unified GPU memory the `large` tier unlocks longer clips and the experimental LTX-2.5 backend (`MEDIA_VIDEO_BACKEND=ltx-2.5`, see `requirements-ltx25.txt`). Run it as a service (systemd example):
```
[Service]
WorkingDirectory=/opt/media-studio
ExecStart=/opt/media-studio/.venv/bin/python run.py
Restart=on-failure
```
Run **one** process (the queue and SQLite are in-process); scale vertically, or run separate instances per GPU on different ports.

## 7. Embedding the UI
The UI is served at `/` and sends `frame-ancestors 'none'` (cannot be iframed). For an iframe inside Enfactum OS, change that CSP in `studio/api.py` and keep the app behind your auth. Linking to it in a new tab works as-is.

## 8. Before real users
* **Moderation** – the built-in policy is a minimal blocklist. Add your own review step in the Laravel layer (the FLUX.2 licence asks deployers to apply filters), and decide whether staff may edit photos of real people.
* **Licences** – confirm the licence of each model you ship (FLUX.2 klein 4B is listed as Apache-2.0; check LTX-Video / LTX-2.5 terms for your commercial use) and keep generated-content disclosure rules in mind.
* **Retention** – set `MEDIA_RETENTION_DAYS`; uploaded customer photos live in `data/uploads`.
* **Backups** – `data/studio.db` + `data/media` + `data/uploads` are everything worth saving (the model cache can be re-downloaded).
