"""Job queue with two lanes.

* heavy lane  - AI generation (image / video). One at a time by default; the
                inactive model is unloaded first (important on unified memory).
* light lane  - ffmpeg / Pillow edits. Never wait behind a long generation.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import shutil
import threading
import time
import urllib.request
import uuid
from pathlib import Path

from pydantic import TypeAdapter

from . import media_tools as mt
from .config import Settings
from .policy import Policy, request_hash, validate_callback, validate_prompt
from .providers.base import JobContext, Provider
from .schemas import HEAVY_KINDS, ImageOp, JobKind, VideoOp
from .storage import JobStore

log = logging.getLogger("studio.worker")

_VIDEO_OPS = TypeAdapter(list[VideoOp])
_IMAGE_OPS = TypeAdapter(list[ImageOp])


_PREFIX = {"image": "i", "video": "v", "image_adjust": "ia", "video_edit": "ve"}


class QueueFull(Exception):
    pass


class JobManager:
    def __init__(self, settings: Settings, store: JobStore, providers: dict[str, Provider]):
        self.s = settings
        self.store = store
        self.providers = providers
        self.policy = Policy()
        self.queues: dict[str, asyncio.Queue] = {}
        self.tasks: list[asyncio.Task] = []
        self.cancel_events: dict[str, threading.Event] = {}

    # ---------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        self.queues = {"heavy": asyncio.Queue(), "light": asyncio.Queue()}
        self.store.recover_running()
        self.store.purge_older_than(self.s.retention_days)
        for row in self.store.queued():
            self.queues[self._lane(row["kind"])].put_nowait(row["id"])
        for lane, n in (("heavy", self.s.max_concurrent_heavy), ("light", self.s.max_concurrent_light)):
            for i in range(n):
                self.tasks.append(asyncio.create_task(self._loop(lane), name=f"studio-{lane}-{i}"))

    async def stop(self) -> None:
        for ev in self.cancel_events.values():
            ev.set()
        for t in self.tasks:
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    @staticmethod
    def _lane(kind: str) -> str:
        return "heavy" if JobKind(kind) in HEAVY_KINDS else "light"

    # ------------------------------------------------------------------- submit
    def _asset(self, owner: str, asset_id: str, want: str) -> dict:
        a = self.store.get_asset(asset_id, owner)
        if not a:
            raise ValueError(f"asset '{asset_id}' not found (upload it first)")
        if a["kind"] != want:
            raise ValueError(f"asset '{asset_id}' is a {a['kind']}, expected {want}")
        return a

    def _check_source(self, owner: str, payload: dict, want: str) -> None:
        if payload.get("source_asset_id"):
            self._asset(owner, payload["source_asset_id"], want)
            return
        job = self.store.get_job(payload["source_job_id"], owner)
        arts = (job or {}).get("artifacts") or []
        idx = payload.get("source_index", 0)
        if not job or job["status"] != "succeeded" or idx >= len(arts):
            raise ValueError("source job not found or has no finished output")
        if not arts[idx]["mime"].startswith(want + "/") and not (want == "image" and arts[idx]["mime"] == "image/gif"):
            raise ValueError(f"source output is not a {want}")

    def submit(self, kind: str, owner: str, payload: dict, idempotency_key: str | None = None) -> dict:
        k = JobKind(kind)
        payload = dict(payload)
        model = payload.pop("model", None)
        callback = payload.get("callback_url")
        validate_callback(callback, self.s.callback_hosts)
        provider = self.providers.get(k.value)
        model_key = provider.key if provider else k.value

        if k in HEAVY_KINDS:
            if model not in (None, "", "default", provider.key):
                raise ValueError(f"unknown model '{model}' for {k.value}; this server runs '{provider.key}'")
            refs = payload.get("reference_asset_ids") or ([payload["reference_asset_id"]] if payload.get("reference_asset_id") else [])
            validate_prompt(payload["prompt"], self.policy, is_edit=bool(refs))
            for ref in refs:
                self._asset(owner, ref, "image")
            if refs and k is JobKind.image and not provider.supports_edit:
                raise ValueError("the active image model does not support reference images")
            if k is JobKind.image:
                if (payload.get("width") or 0) * (payload.get("height") or 0) > self.s.max_image_pixels:
                    raise ValueError("image resolution exceeds the configured limit")
            else:
                if payload.get("reference_asset_id") and not provider.supports_start_frame:
                    raise ValueError("the active video model does not support a start frame")
                if (payload.get("width") or 0) * (payload.get("height") or 0) > self.s.max_video_pixels:
                    raise ValueError("video resolution exceeds the configured limit")
                frames = payload.get("frames")
                if frames is None:
                    d = payload.get("duration_seconds")
                    fps = payload.get("fps", 24)
                    frames = max(9, round(d * fps / 8) * 8 + 1) if d else getattr(provider, "default_frames", 49)
                if frames > self.s.max_video_frames:
                    raise ValueError(f"video exceeds the frame limit ({self.s.max_video_frames})")
                payload["frames"] = frames
                payload.pop("duration_seconds", None)
        else:
            self._check_source(owner, payload, "video" if k is JobKind.video_edit else "image")

        lane = self._lane(k.value)
        if self.queues[lane].qsize() >= self.s.queue_size:
            raise QueueFull("generation queue is full - try again shortly")

        job_id = f"{_PREFIX[k.value]}_{uuid.uuid4().hex[:20]}"
        row, created = self.store.create_job(
            job_id=job_id, kind=k.value, owner_id=owner, request_hash=request_hash({"kind": k.value, **payload}),
            model=model_key, payload=payload, idempotency_key=idempotency_key or payload.get("idempotency_key"))
        if created:
            self.queues[lane].put_nowait(row["id"])
        return row

    # ---------------------------------------------------------------- execution
    async def _loop(self, lane: str) -> None:
        q = self.queues[lane]
        while True:
            job_id = await q.get()
            try:
                await self._execute(job_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("unhandled error in job %s", job_id)
            finally:
                q.task_done()

    def _reporter(self, job_id: str):
        last = [0.0]

        def report(frac: float, message: str = "") -> None:
            now = time.monotonic()
            if now - last[0] < 0.25 and frac < 0.99:
                return
            last[0] = now
            self.store.update_job(job_id, progress=round(max(0.0, min(frac, 0.99)), 4), message=message)
        return report

    async def _execute(self, job_id: str) -> None:
        row = self.store.get_job(job_id)
        if not row or row["status"] != "queued":
            return  # cancelled while waiting
        ev = threading.Event()
        self.cancel_events[job_id] = ev
        self.store.update_job(job_id, status="running", started_at=time.time(), progress=0.01, message="Starting")
        out_dir = self.store.media / job_id
        out_dir.mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        try:
            specs = await asyncio.to_thread(self._work, row, ev, out_dir)
            arts = await asyncio.to_thread(self._finalize, job_id, specs)
            meta = {"seconds": round(time.time() - t0, 2), "sha256": arts[0].get("sha256") if arts else None,
                    "seeds": [a["seed"] for a in arts if a.get("seed") is not None]}
            prov = self.providers.get(row["kind"])
            if prov:
                meta.update(provider=prov.key, mock=prov.is_mock)
            self.store.update_job(job_id, status="succeeded", finished_at=time.time(), progress=1.0,
                                  message="Done", artifacts=arts, metadata=meta)
        except mt.Cancel:
            shutil.rmtree(out_dir, ignore_errors=True)
            self.store.update_job(job_id, status="cancelled", finished_at=time.time(), message="Cancelled")
        except Exception as exc:
            log.exception("job %s failed", job_id)
            shutil.rmtree(out_dir, ignore_errors=True)
            self.store.update_job(job_id, status="failed", finished_at=time.time(), progress=1.0,
                                  message="Failed", error=f"{type(exc).__name__}: {str(exc)[:600]}")
        finally:
            self.cancel_events.pop(job_id, None)
            final = self.store.get_job(job_id)
            if final and final["payload"].get("callback_url"):
                asyncio.create_task(asyncio.to_thread(self._notify, final))

    # ---- thread side
    def _source_path(self, owner: str, payload: dict) -> Path:
        if payload.get("source_asset_id"):
            a = self.store.get_asset(payload["source_asset_id"], owner)
            p = self.store.asset_path(a) if a else None
        else:
            hit = self.store.artifact_file(payload["source_job_id"], owner, payload.get("source_index", 0))
            p = hit[0] if hit else None
        if not p:
            raise FileNotFoundError("source media is no longer available")
        return p

    def _work(self, row: dict, ev: threading.Event, out_dir: Path) -> list[dict]:
        kind, payload, owner, job_id = row["kind"], row["payload"], row["owner_id"], row["id"]
        report = self._reporter(job_id)
        cancelled = ev.is_set

        if kind in ("image", "video"):
            provider = self.providers[kind]
            if self.s.max_concurrent_heavy == 1:  # keep one heavy model resident
                for other_kind, other in self.providers.items():
                    if other_kind != kind:
                        other.unload()
            ctx = JobContext(
                job_id=job_id, payload=payload, out_dir=out_dir, report=report, is_cancelled=cancelled,
                asset_path=lambda aid: self._resolve_asset(owner, aid))
            return provider.generate(ctx)

        src = self._source_path(owner, payload)
        if kind == "video_edit":
            ops = _VIDEO_OPS.validate_python(payload.get("ops", []))
            fmt = payload.get("output", "mp4")
            ext, mime = {"mp4": ("mp4", "video/mp4"), "gif": ("gif", "image/gif"), "frame": ("png", "image/png")}[fmt]
            out = out_dir / f"0.{ext}"
            meta = mt.edit_video(src, out, ops, output=fmt, frame_time=float(payload.get("frame_time", 0)),
                                 progress=lambda f: report(f, "Rendering"), cancelled=cancelled)
            return [{"path": out, "mime": mime, **meta}]
        if kind == "image_adjust":
            ops = _IMAGE_OPS.validate_python(payload["ops"])
            fmt = payload.get("format", "png")
            out = out_dir / f"0.{'jpg' if fmt == 'jpeg' else fmt}"
            report(0.3, "Applying adjustments")
            meta = mt.adjust_image(src, out, ops, fmt)
            return [{"path": out, "mime": f"image/{fmt}", **meta}]
        raise ValueError(f"unsupported job kind {kind}")

    def _resolve_asset(self, owner: str, asset_id: str) -> Path:
        a = self.store.get_asset(asset_id, owner)
        p = self.store.asset_path(a) if a else None
        if not p:
            raise FileNotFoundError(f"asset {asset_id} is no longer available")
        return p

    def _finalize(self, job_id: str, specs: list[dict]) -> list[dict]:
        arts = []
        for i, sp in enumerate(specs):
            path: Path = Path(sp.pop("path"))
            thumb = path.with_name(f"{path.stem}.thumb.jpg")
            try:
                if sp["mime"].startswith("video/"):
                    mt.video_thumbnail(path, thumb)
                else:
                    mt.image_thumbnail(path, thumb)
            except Exception:
                log.warning("thumbnail failed for %s", path, exc_info=True)
                thumb = None
            arts.append({
                **sp, "path": f"{job_id}/{path.name}", "thumb": f"{job_id}/{thumb.name}" if thumb else None,
                "bytes": path.stat().st_size, "sha256": self.store.checksum(path) if i == 0 else None})
        return arts

    # ---- webhook
    def _notify(self, row: dict) -> None:
        body = json.dumps({"event": f"job.{row['status']}", "job": self.public(row)}).encode()
        headers = {"Content-Type": "application/json", "User-Agent": "genai-media-studio"}
        if self.s.api_token:
            sig = hmac.new(self.s.api_token.encode(), body, hashlib.sha256).hexdigest()
            headers["X-Media-Signature"] = f"sha256={sig}"
        for attempt in range(3):
            try:
                req = urllib.request.Request(row["payload"]["callback_url"], data=body, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=8):
                    return
            except Exception as exc:
                log.warning("callback attempt %d for %s failed: %s", attempt + 1, row["id"], exc)
                time.sleep(2 ** attempt)

    # ---------------------------------------------------------------- user ops
    def cancel(self, job_id: str, owner: str) -> dict | None:
        row = self.store.get_job(job_id, owner)
        if not row:
            return None
        if row["status"] == "queued":
            self.store.update_job(job_id, status="cancelled", finished_at=time.time(), message="Cancelled")
        elif row["status"] == "running" and job_id in self.cancel_events:
            self.cancel_events[job_id].set()
            self.store.update_job(job_id, message="Cancelling…")
        return self.store.get_job(job_id, owner)

    def queue_depth(self) -> dict:
        return {lane: q.qsize() for lane, q in self.queues.items()}

    # ------------------------------------------------------------------ output
    def public(self, row: dict) -> dict:
        jid = row["id"]
        arts = [{
            "index": i, "url": f"/v1/media/{jid}/{i}", "thumb_url": f"/v1/media/{jid}/{i}/thumb",
            "mime": a.get("mime"), "width": a.get("width"), "height": a.get("height"),
            "duration": a.get("duration"), "bytes": a.get("bytes"),
        } for i, a in enumerate(row.get("artifacts") or [])]
        return {
            "job_id": jid, "kind": row["kind"], "status": row["status"], "model": row["model"],
            "created_at": row["created_at"], "started_at": row.get("started_at"),
            "finished_at": row.get("finished_at"), "progress": row.get("progress") or 0.0,
            "message": row.get("message") or "", "error": row.get("error"), "artifacts": arts,
            "artifact_url": arts[0]["url"] if arts else None,
            "artifact_mime": arts[0]["mime"] if arts else None,
            "params": {k: v for k, v in (row.get("payload") or {}).items() if k != "callback_url"},
            "metadata": row.get("metadata") or {},
        }
