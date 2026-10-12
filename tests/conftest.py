import io
import os
import sys
import time
from pathlib import Path

os.environ["MEDIA_MOCK_DELAY"] = "0"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from studio.api import create_app
from studio.config import Settings


def make_settings(tmp_path, **kw):
    base = dict(data_dir=tmp_path / "data", model_cache=tmp_path / "models",
                image_backend="mock", video_backend="mock")
    base.update(kw)
    return Settings(**base)


@pytest.fixture
def make_client(tmp_path):
    clients = []

    def factory(**kw):
        app = create_app(make_settings(tmp_path, **kw))
        c = TestClient(app)
        c.__enter__()
        clients.append(c)
        return c

    yield factory
    for c in clients:
        c.__exit__(None, None, None)


@pytest.fixture
def client(make_client):
    return make_client()


def png_bytes(size=(300, 200), color=(10, 120, 200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def wait_job(c, job_id, timeout=20, headers=None):
    end = time.time() + timeout
    j = {}
    while time.time() < end:
        j = c.get(f"/v1/jobs/{job_id}", headers=headers).json()
        if j.get("status") in ("succeeded", "failed", "cancelled"):
            return j
        time.sleep(0.03)
    raise AssertionError(f"job {job_id} did not finish: {j}")


def upload(c, data=None, name="x.png", mime="image/png", headers=None):
    return c.post("/v1/assets", files={"file": (name, data or png_bytes(), mime)}, headers=headers)
