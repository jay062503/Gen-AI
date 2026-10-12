"""SQLite persistence for jobs and uploaded assets (thread-safe, WAL mode)."""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

ACTIVE = ("queued", "running")


class JobStore:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.media = self.root / "media"
        self.uploads = self.root / "uploads"
        for d in (self.root, self.media, self.uploads):
            d.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(self.root / "studio.db", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init()

    def _init(self):
        with self.lock:
            self.conn.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS jobs (
              id TEXT PRIMARY KEY,
              kind TEXT NOT NULL,
              owner_id TEXT NOT NULL,
              request_hash TEXT NOT NULL,
              idempotency_key TEXT,
              status TEXT NOT NULL,
              model TEXT NOT NULL,
              payload TEXT NOT NULL,
              created_at REAL NOT NULL,
              started_at REAL,
              finished_at REAL,
              progress REAL NOT NULL DEFAULT 0,
              message TEXT NOT NULL DEFAULT '',
              error TEXT,
              artifacts TEXT NOT NULL DEFAULT '[]',
              metadata TEXT NOT NULL DEFAULT '{}'
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_idem
              ON jobs(owner_id, idempotency_key) WHERE idempotency_key IS NOT NULL;
            CREATE INDEX IF NOT EXISTS idx_jobs_owner ON jobs(owner_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, created_at);
            CREATE TABLE IF NOT EXISTS assets (
              id TEXT PRIMARY KEY,
              owner_id TEXT NOT NULL,
              kind TEXT NOT NULL,
              path TEXT NOT NULL,
              thumb_path TEXT,
              mime TEXT NOT NULL,
              filename TEXT NOT NULL,
              bytes INTEGER NOT NULL,
              width INTEGER, height INTEGER, duration REAL,
              has_audio INTEGER NOT NULL DEFAULT 0,
              created_at REAL NOT NULL
            );
            """)
            self.conn.commit()

    # ------------------------------------------------------------------ jobs
    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        d = dict(row)
        for k in ("payload", "artifacts", "metadata"):
            try:
                d[k] = json.loads(d[k]) if isinstance(d.get(k), str) else d.get(k)
            except Exception:
                d[k] = [] if k == "artifacts" else {}
        return d

    def create_job(self, *, job_id: str, kind: str, owner_id: str, request_hash: str, model: str,
                   payload: dict, idempotency_key: str | None = None) -> tuple[dict, bool]:
        with self.lock:
            if idempotency_key:
                row = self.conn.execute("SELECT * FROM jobs WHERE owner_id=? AND idempotency_key=?",
                                        (owner_id, idempotency_key)).fetchone()
                if row:
                    return self._row(row), False
            self.conn.execute(
                "INSERT INTO jobs(id,kind,owner_id,request_hash,idempotency_key,status,model,payload,created_at,message)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (job_id, kind, owner_id, request_hash, idempotency_key, "queued", model,
                 json.dumps(payload), time.time(), "Waiting in queue"))
            self.conn.commit()
            return self._row(self.conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()), True

    def get_job(self, job_id: str, owner_id: str | None = None) -> dict | None:
        with self.lock:
            if owner_id is None:
                row = self.conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            else:
                row = self.conn.execute("SELECT * FROM jobs WHERE id=? AND owner_id=?", (job_id, owner_id)).fetchone()
        return self._row(row)

    def list_jobs(self, owner_id: str, *, kind: str | None = None, limit: int = 50) -> list[dict]:
        q, args = "SELECT * FROM jobs WHERE owner_id=?", [owner_id]
        if kind:
            q += " AND kind=?"
            args.append(kind)
        q += " ORDER BY created_at DESC LIMIT ?"
        args.append(max(1, min(limit, 200)))
        with self.lock:
            return [self._row(r) for r in self.conn.execute(q, args).fetchall()]

    def update_job(self, job_id: str, **fields: Any) -> None:
        allowed = {"status", "started_at", "finished_at", "progress", "message", "error", "artifacts", "metadata"}
        fields = {k: v for k, v in fields.items() if k in allowed}
        if not fields:
            return
        for k in ("artifacts", "metadata"):
            if k in fields and not isinstance(fields[k], str):
                fields[k] = json.dumps(fields[k])
        sets = ",".join(f"{k}=?" for k in fields)
        with self.lock:
            self.conn.execute(f"UPDATE jobs SET {sets} WHERE id=?", (*fields.values(), job_id))
            self.conn.commit()

    def delete_job(self, job_id: str, owner_id: str) -> bool:
        job = self.get_job(job_id, owner_id)
        if not job or job["status"] in ACTIVE:
            return False
        shutil.rmtree(self.media / job_id, ignore_errors=True)
        with self.lock:
            self.conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
            self.conn.commit()
        return True

    def recover_running(self) -> None:
        with self.lock:
            self.conn.execute("UPDATE jobs SET status='queued', started_at=NULL, progress=0,"
                              " message='Re-queued after restart' WHERE status='running'")
            self.conn.commit()

    def queued(self) -> list[dict]:
        with self.lock:
            rows = self.conn.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at").fetchall()
        return [self._row(r) for r in rows]

    def artifact_file(self, job_id: str, owner_id: str, index: int = 0, thumb: bool = False):
        job = self.get_job(job_id, owner_id)
        if not job:
            return None
        arts = job["artifacts"] or []
        if index < 0 or index >= len(arts):
            return None
        art = arts[index]
        rel = art.get("thumb") if thumb else art.get("path")
        if not rel:
            return None
        path = (self.media / rel).resolve()
        if not path.is_relative_to(self.media) or not path.is_file():
            return None
        return path, ("image/jpeg" if thumb else art.get("mime", "application/octet-stream")), art

    # ---------------------------------------------------------------- assets
    def add_asset(self, rec: dict) -> dict:
        cols = ("id", "owner_id", "kind", "path", "thumb_path", "mime", "filename", "bytes",
                "width", "height", "duration", "has_audio", "created_at")
        with self.lock:
            self.conn.execute(f"INSERT INTO assets({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                              tuple(rec.get(c) for c in cols))
            self.conn.commit()
        return rec

    def get_asset(self, asset_id: str, owner_id: str | None = None) -> dict | None:
        with self.lock:
            if owner_id is None:
                row = self.conn.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
            else:
                row = self.conn.execute("SELECT * FROM assets WHERE id=? AND owner_id=?", (asset_id, owner_id)).fetchone()
        return dict(row) if row else None

    def asset_path(self, asset: dict, thumb: bool = False) -> Path | None:
        rel = asset.get("thumb_path") if thumb else asset.get("path")
        if not rel:
            return None
        p = (self.uploads / rel).resolve()
        return p if p.is_relative_to(self.uploads) and p.is_file() else None

    # ------------------------------------------------------------- retention
    def purge_older_than(self, days: int) -> int:
        if days <= 0:
            return 0
        cutoff = time.time() - days * 86400
        with self.lock:
            ids = [r["id"] for r in self.conn.execute(
                "SELECT id FROM jobs WHERE created_at<? AND status NOT IN ('queued','running')", (cutoff,))]
            assets = [dict(r) for r in self.conn.execute("SELECT * FROM assets WHERE created_at<?", (cutoff,))]
            for jid in ids:
                shutil.rmtree(self.media / jid, ignore_errors=True)
            for a in assets:
                for rel in (a["path"], a["thumb_path"]):
                    if rel:
                        (self.uploads / rel).unlink(missing_ok=True)
            self.conn.execute("DELETE FROM jobs WHERE created_at<? AND status NOT IN ('queued','running')", (cutoff,))
            self.conn.execute("DELETE FROM assets WHERE created_at<?", (cutoff,))
            self.conn.commit()
        return len(ids)

    @staticmethod
    def checksum(path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
