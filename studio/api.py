"""HTTP API + web UI.  Create with ``create_app()``; run with ``python run.py``."""
from __future__ import annotations

import hashlib
import hmac
import logging
import re
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps
from pydantic import BaseModel

from . import __version__, hardware
from . import media_tools as mt
from .config import Settings, load_settings
from .providers.registry import build_providers
from .schemas import ImageAdjustRequest, ImageJobRequest, JobResponse, VideoEditRequest, VideoJobRequest
from .storage import JobStore
from .worker import JobManager, QueueFull

log = logging.getLogger("studio.api")
STATIC = Path(__file__).resolve().parent / "static"
SESSION_COOKIE = "studio_session"
OWNER_RE = re.compile(r"^[A-Za-z0-9_.:@-]{1,64}$")
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".heic", ".heif"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
VIDEO_MIME = {".mp4": "video/mp4", ".mov": "video/quicktime", ".m4v": "video/mp4",
              ".webm": "video/webm", ".mkv": "video/x-matroska"}
IMAGE_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


class SessionIn(BaseModel):
    token: str


def _snap(v: float, m: int) -> int:
    return max(m, int(round(v / m)) * m)


def _presets(area: int, multiple: int, ratios: list[tuple[str, int, int]]) -> list[dict]:
    out = []
    for name, rw, rh in ratios:
        w = _snap((area * rw / rh) ** 0.5, multiple)
        h = _snap((area * rh / rw) ** 0.5, multiple)
        out.append({"label": name, "width": w, "height": h})
    return out


def create_app(settings: Settings | None = None) -> FastAPI:
    s = settings or load_settings()
    if not s.api_token and not s.is_loopback and not s.allow_insecure:
        raise RuntimeError(
            f"Refusing to listen on {s.host} without MEDIA_API_TOKEN. Set a token in .env "
            "(or MEDIA_ALLOW_INSECURE=1 if the network is fully trusted).")

    store = JobStore(s.data_dir)
    providers = build_providers(s)
    manager = JobManager(s, store, providers)
    hw = hardware.detect()
    tier = hardware.tier(hw)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await manager.start()
        yield
        await manager.stop()

    app = FastAPI(title="GenAI Media Studio", version=__version__, lifespan=lifespan)
    app.state.settings, app.state.store, app.state.manager = s, store, manager

    if s.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=list(s.cors_origins), allow_methods=["*"],
                           allow_headers=["*"], allow_credentials=False)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        if request.url.path == "/":
            resp.headers["Content-Security-Policy"] = (
                "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
                "style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; "
                "frame-ancestors 'none'; base-uri 'none'")
            resp.headers["Cache-Control"] = "no-store"
        return resp

    # ------------------------------------------------------------------ auth
    def _session_value() -> str:
        return hmac.new(s.api_token.encode(), b"studio-session", hashlib.sha256).hexdigest()

    async def auth(request: Request) -> str:
        """Returns the owner id. Server-to-server callers may scope data per user via X-Owner-Id."""
        if not s.api_token:
            return "local"
        supplied = request.headers.get("x-media-token") or ""
        bearer = request.headers.get("authorization", "")
        if not supplied and bearer.lower().startswith("bearer "):
            supplied = bearer[7:].strip()
        if supplied and hmac.compare_digest(supplied, s.api_token):
            owner = request.headers.get("x-owner-id")
            if owner:
                if not OWNER_RE.match(owner):
                    raise HTTPException(400, "invalid X-Owner-Id")
                return owner
            return "local"
        cookie = request.cookies.get(SESSION_COOKIE, "")
        if cookie and hmac.compare_digest(cookie, _session_value()):
            return "local"
        raise HTTPException(401, "invalid or missing credentials")

    Owner = Depends(auth)

    @app.post("/v1/session")
    def login(body: SessionIn, response: Response):
        if not s.api_token:
            return {"auth": False}
        if not hmac.compare_digest(body.token, s.api_token):
            raise HTTPException(401, "invalid token")
        response.set_cookie(SESSION_COOKIE, _session_value(), httponly=True, samesite="strict",
                            max_age=30 * 86400)
        return {"auth": True}

    @app.delete("/v1/session")
    def logout(response: Response):
        response.delete_cookie(SESSION_COOKIE)
        return {"ok": True}

    # ---------------------------------------------------------------- basics
    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index():
        return (STATIC / "index.html").read_text(encoding="utf-8")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/health")
    def health():
        return {"status": "ok", "version": __version__, "auth_required": bool(s.api_token),
                "mode": "demo" if any(p.is_mock for p in providers.values()) else "ai"}

    @app.get("/v1/capabilities")
    def capabilities(_: str = Owner):
        img, vid = providers["image"], providers["video"]
        lim = hardware.TIER_LIMITS[tier]
        vw, vh = getattr(vid, "default_size", lim["video_default"])
        side = getattr(img, "default_side", lim["image_default"])
        return {
            "version": __version__, "tier": tier,
            "mode": "demo" if (img.is_mock or vid.is_mock) else "ai",
            "hardware": {k: hw[k] for k in ("os", "arch", "device", "device_name", "memory_gb", "ram_gb", "torch", "diffusers")},
            "image": {**img.describe(), "default_side": side,
                      "presets": _presets(side * side, 16, [("Square", 1, 1), ("Landscape", 16, 9), ("Portrait", 9, 16),
                                                           ("Photo", 3, 2), ("Tall", 4, 5)])},
            "video": {**vid.describe(), "default_frames": getattr(vid, "default_frames", 49),
                      "max_frames": s.max_video_frames,
                      "presets": _presets(vw * vh, 32, [("Landscape", 16, 9), ("Portrait", 9, 16), ("Square", 1, 1)])},
            "limits": {"max_image_upload_mb": s.max_image_upload_mb, "max_video_upload_mb": s.max_video_upload_mb,
                       "max_image_pixels": s.max_image_pixels, "max_video_pixels": s.max_video_pixels},
            "queue": manager.queue_depth(), "auth_required": bool(s.api_token),
        }

    # ---------------------------------------------------------------- assets
    @app.post("/v1/assets", status_code=201)
    async def upload_asset(file: UploadFile = File(...), owner: str = Owner):
        name = Path(file.filename or "upload").name
        ext = Path(name).suffix.lower()
        if ext in IMAGE_EXT:
            kind, limit = "image", s.max_image_upload_mb
        elif ext in VIDEO_EXT:
            kind, limit = "video", s.max_video_upload_mb
        else:
            raise HTTPException(422, "unsupported file type - use PNG, JPEG, WebP, HEIC, MP4, MOV, M4V, WebM or MKV")
        asset_id = f"a_{uuid.uuid4().hex[:20]}"
        raw = store.uploads / f"{asset_id}{ext}"
        size, cap = 0, limit * 1024 * 1024
        try:
            with raw.open("wb") as fh:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > cap:
                        raise HTTPException(413, f"file exceeds {limit} MB")
                    fh.write(chunk)
            rec = await run_in_threadpool(_ingest, raw, asset_id, ext, kind, name, size, owner)
        except HTTPException:
            raw.unlink(missing_ok=True)
            raise
        except Exception as exc:
            raw.unlink(missing_ok=True)
            log.info("rejected upload %s: %s", name, exc)
            raise HTTPException(422, f"could not read this {kind}: {exc}") from exc
        return _asset_out(rec)

    def _ingest(raw: Path, asset_id: str, ext: str, kind: str, name: str, size: int, owner: str) -> dict:
        thumb = store.uploads / f"{asset_id}.thumb.jpg"
        rec = {"id": asset_id, "owner_id": owner, "kind": kind, "filename": name, "bytes": size,
               "created_at": time.time(), "has_audio": 0, "duration": None}
        if kind == "image":
            with Image.open(raw) as im:
                im.load()
                im = ImageOps.exif_transpose(im)
                w, h = im.size
                if ext in {".heic", ".heif"}:  # browsers cannot show HEIC - store a JPEG instead
                    conv = raw.with_suffix(".jpg")
                    im.convert("RGB").save(conv, "JPEG", quality=95)
                    raw.unlink(missing_ok=True)
                    raw, ext = conv, ".jpg"
            mt.image_thumbnail(raw, thumb)
            rec.update(path=raw.name, mime=IMAGE_MIME[ext], width=w, height=h)
        else:
            info = mt.probe(raw)
            if not info["has_video"]:
                raise ValueError("no video stream found")
            mt.video_thumbnail(raw, thumb)
            rec.update(path=raw.name, mime=VIDEO_MIME[ext], width=info["width"], height=info["height"],
                       duration=info["duration"], has_audio=int(info["has_audio"]))
        rec["thumb_path"] = thumb.name
        return store.add_asset(rec)

    def _asset_out(a: dict) -> dict:
        return {"asset_id": a["id"], "kind": a["kind"], "filename": a["filename"], "mime": a["mime"],
                "width": a["width"], "height": a["height"], "duration": a["duration"],
                "has_audio": bool(a["has_audio"]), "bytes": a["bytes"],
                "url": f"/v1/assets/{a['id']}", "thumb_url": f"/v1/assets/{a['id']}/thumb"}

    def _asset_file(asset_id: str, owner: str, thumb: bool) -> FileResponse:
        a = store.get_asset(asset_id, owner)
        p = store.asset_path(a, thumb) if a else None
        if not p:
            raise HTTPException(404, "asset not found")
        return FileResponse(p, media_type="image/jpeg" if thumb else a["mime"], content_disposition_type="inline")

    @app.get("/v1/assets/{asset_id}")
    def get_asset(asset_id: str, owner: str = Owner):
        return _asset_file(asset_id, owner, False)

    @app.get("/v1/assets/{asset_id}/thumb")
    def get_asset_thumb(asset_id: str, owner: str = Owner):
        return _asset_file(asset_id, owner, True)

    @app.get("/v1/assets/{asset_id}/info")
    def get_asset_info(asset_id: str, owner: str = Owner):
        a = store.get_asset(asset_id, owner)
        if not a:
            raise HTTPException(404, "asset not found")
        return _asset_out(a)

    # ------------------------------------------------------------------ jobs
    def _submit(kind: str, req, owner: str):
        try:
            row = manager.submit(kind, owner, req.model_dump(mode="json", exclude_none=True))
        except QueueFull as exc:
            raise HTTPException(429, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return JSONResponse(manager.public(row), status_code=202)

    @app.post("/v1/images/jobs", response_model=JobResponse, status_code=202)
    def create_image(req: ImageJobRequest, owner: str = Owner):
        return _submit("image", req, owner)

    @app.post("/v1/videos/jobs", response_model=JobResponse, status_code=202)
    def create_video(req: VideoJobRequest, owner: str = Owner):
        return _submit("video", req, owner)

    @app.post("/v1/images/adjust", response_model=JobResponse, status_code=202)
    def adjust_image(req: ImageAdjustRequest, owner: str = Owner):
        return _submit("image_adjust", req, owner)

    @app.post("/v1/videos/edit", response_model=JobResponse, status_code=202)
    def edit_video(req: VideoEditRequest, owner: str = Owner):
        return _submit("video_edit", req, owner)

    @app.get("/v1/jobs")
    def list_jobs(kind: str | None = None, limit: int = 50, owner: str = Owner):
        return {"jobs": [manager.public(r) for r in store.list_jobs(owner, kind=kind, limit=limit)]}

    @app.get("/v1/jobs/{job_id}", response_model=JobResponse)
    def get_job(job_id: str, owner: str = Owner):
        row = store.get_job(job_id, owner)
        if not row:
            raise HTTPException(404, "job not found")
        return manager.public(row)

    @app.post("/v1/jobs/{job_id}/cancel", response_model=JobResponse)
    def cancel_job(job_id: str, owner: str = Owner):
        row = manager.cancel(job_id, owner)
        if not row:
            raise HTTPException(404, "job not found")
        return manager.public(row)

    @app.delete("/v1/jobs/{job_id}")
    def delete_job(job_id: str, owner: str = Owner):
        if not store.get_job(job_id, owner):
            raise HTTPException(404, "job not found")
        if not store.delete_job(job_id, owner):
            raise HTTPException(409, "job is still running - cancel it first")
        return {"deleted": job_id}

    # ----------------------------------------------------------------- media
    def _slug(text: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "media"

    def _media(job_id: str, index: int, thumb: bool, download: bool, owner: str):
        hit = store.artifact_file(job_id, owner, index, thumb)
        if not hit:
            raise HTTPException(404, "artifact not found")
        path, mime, art = hit
        if thumb:
            return FileResponse(path, media_type=mime, content_disposition_type="inline")
        job = store.get_job(job_id, owner) or {}
        base = _slug(str((job.get("payload") or {}).get("prompt") or job.get("kind") or "media"))
        n = len(job.get("artifacts") or [])
        name = f"{base}-{job_id[-6:]}{f'-{index + 1}' if n > 1 else ''}{path.suffix}"
        return FileResponse(path, media_type=mime, filename=name,
                            content_disposition_type="attachment" if download else "inline")

    @app.get("/v1/media/{job_id}")
    def media_first(job_id: str, download: bool = False, owner: str = Owner):
        return _media(job_id, 0, False, download, owner)

    @app.get("/v1/media/{job_id}/{index}")
    def media_item(job_id: str, index: int, download: bool = False, owner: str = Owner):
        return _media(job_id, index, False, download, owner)

    @app.get("/v1/media/{job_id}/{index}/thumb")
    def media_thumb(job_id: str, index: int, owner: str = Owner):
        return _media(job_id, index, True, False, owner)

    return app
