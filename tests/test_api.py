import time

import studio.providers.mock as mock
from conftest import make_settings, png_bytes, upload, wait_job


def test_health_and_capabilities(client):
    assert client.get("/health").json()["mode"] == "demo"
    cap = client.get("/v1/capabilities").json()
    assert cap["image"]["supports"]["edit"] and cap["video"]["presets"]
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200


def test_image_generation_and_download(client):
    r = client.post("/v1/images/jobs", json={"prompt": "red fox", "count": 2, "seed": 7})
    assert r.status_code == 202
    j = wait_job(client, r.json()["job_id"])
    assert j["status"] == "succeeded" and len(j["artifacts"]) == 2
    assert j["metadata"]["seeds"] == [7, 8]
    m = client.get(j["artifacts"][0]["url"])
    assert m.headers["content-type"] == "image/png"
    d = client.get(j["artifacts"][1]["url"] + "?download=1")
    assert "attachment" in d.headers["content-disposition"] and "red-fox" in d.headers["content-disposition"]
    assert client.get(j["artifacts"][0]["thumb_url"]).headers["content-type"] == "image/jpeg"
    assert client.get(f"/v1/media/{j['job_id']}").status_code == 200   # first-artifact alias


def test_ai_edit_uses_reference_and_matches_aspect(client):
    a = upload(client, png_bytes((600, 300))).json()
    j = wait_job(client, client.post("/v1/images/jobs", json={
        "prompt": "make it sunset", "reference_asset_ids": [a["asset_id"]]}).json()["job_id"])
    art = j["artifacts"][0]
    assert j["status"] == "succeeded" and abs(art["width"] / art["height"] - 2) < 0.1


def test_unknown_reference_model_and_blocked_prompt_rejected(client):
    assert client.post("/v1/images/jobs", json={"prompt": "x", "reference_asset_ids": ["nope"]}).status_code == 422
    assert client.post("/v1/images/jobs", json={"prompt": "x", "model": "sdxl"}).status_code == 422
    assert client.post("/v1/images/jobs", json={"prompt": "child porn"}).status_code == 422


def test_idempotency_key(client):
    body = {"prompt": "same", "idempotency_key": "abc-1"}
    a = client.post("/v1/images/jobs", json=body).json()
    b = client.post("/v1/images/jobs", json=body).json()
    assert a["job_id"] == b["job_id"]
    c = client.post("/v1/images/jobs", json={"prompt": "same"}).json()   # no key -> new job
    assert c["job_id"] != a["job_id"]


def test_upload_validation(client, make_client):
    assert upload(client, b"hello", "a.txt", "text/plain").status_code == 422
    assert upload(client, b"not really a png", "a.png").status_code == 422
    small = make_client(max_image_upload_mb=1)
    assert upload(small, b"0" * (1024 * 1024 + 10), "big.png").status_code == 413
    r = upload(client, png_bytes((50, 40)), "../../evil.png")
    assert r.status_code == 201 and r.json()["filename"] == "evil.png"


def test_video_flow_range_and_edits(client):
    v = wait_job(client, client.post("/v1/videos/jobs", json={"prompt": "waves", "frames": 25, "fps": 24}).json()["job_id"])
    assert v["status"] == "succeeded" and v["artifacts"][0]["mime"] == "video/mp4"
    rng = client.get(v["artifacts"][0]["url"], headers={"Range": "bytes=0-99"})
    assert rng.status_code == 206 and len(rng.content) == 100

    e = wait_job(client, client.post("/v1/videos/edit", json={
        "source_job_id": v["job_id"],
        "ops": [{"op": "trim", "start": 0.2, "end": 0.8}, {"op": "speed", "factor": 2},
                {"op": "filter", "name": "sepia"}, {"op": "resize", "width": 200, "height": 200, "mode": "fill"},
                {"op": "rotate", "degrees": 90}, {"op": "flip", "axis": "h"}, {"op": "adjust", "brightness": 0.1},
                {"op": "fade", "fade_in": 0.1, "fade_out": 0.1}, {"op": "mute"}]}).json()["job_id"])
    assert e["status"] == "succeeded", e["error"]
    assert (e["artifacts"][0]["width"], e["artifacts"][0]["height"]) == (200, 200)
    assert 0.2 < e["artifacts"][0]["duration"] < 0.5

    rev = wait_job(client, client.post("/v1/videos/edit", json={"source_job_id": v["job_id"], "ops": [{"op": "reverse"}]}).json()["job_id"])
    assert rev["status"] == "succeeded", rev["error"]
    gif = wait_job(client, client.post("/v1/videos/edit", json={"source_job_id": v["job_id"], "output": "gif", "ops": []}).json()["job_id"])
    assert gif["status"] == "succeeded", gif["error"]
    assert gif["artifacts"][0]["mime"] == "image/gif"
    fr = wait_job(client, client.post("/v1/videos/edit", json={"source_job_id": v["job_id"], "output": "frame", "frame_time": 0.5}).json()["job_id"])
    assert fr["status"] == "succeeded", fr["error"]
    assert fr["artifacts"][0]["mime"] == "image/png"


def test_video_edit_on_uploaded_video_and_bad_sources(client):
    v = wait_job(client, client.post("/v1/videos/jobs", json={"prompt": "x", "frames": 17}).json()["job_id"])
    data = client.get(v["artifacts"][0]["url"]).content
    up = client.post("/v1/assets", files={"file": ("clip.mp4", data, "video/mp4")})
    assert up.status_code == 201 and up.json()["kind"] == "video" and up.json()["duration"] > 0
    e = wait_job(client, client.post("/v1/videos/edit", json={"source_asset_id": up.json()["asset_id"], "ops": [{"op": "speed", "factor": 0.5}]}).json()["job_id"])
    assert e["status"] == "succeeded", e["error"]
    img_id = upload(client).json()["asset_id"]
    assert client.post("/v1/videos/edit", json={"source_asset_id": img_id, "ops": [{"op": "mute"}]}).status_code == 422
    assert client.post("/v1/videos/edit", json={"source_job_id": "nope", "ops": [{"op": "mute"}]}).status_code == 422
    assert client.post("/v1/assets", files={"file": ("bad.mp4", b"garbage", "video/mp4")}).status_code == 422


def test_image_adjust(client):
    a = upload(client, png_bytes((400, 200))).json()
    j = wait_job(client, client.post("/v1/images/adjust", json={
        "source_asset_id": a["asset_id"], "format": "jpeg", "ops": [
            {"op": "crop_aspect", "ratio_w": 1, "ratio_h": 1}, {"op": "grayscale"}, {"op": "rotate", "degrees": 90},
            {"op": "adjust", "brightness": 1.2}, {"op": "blur", "radius": 1},
            {"op": "resize", "width": 100, "height": 100, "mode": "fit"}]}).json()["job_id"])
    assert j["status"] == "succeeded", j["error"]
    assert (j["artifacts"][0]["width"], j["artifacts"][0]["height"]) == (100, 100)
    assert j["artifacts"][0]["mime"] == "image/jpeg"


def test_cancel_running_and_queued_then_delete(client, monkeypatch):
    monkeypatch.setattr(mock, "DELAY", 0.15)
    first = client.post("/v1/images/jobs", json={"prompt": "slow", "steps": 40}).json()["job_id"]
    second = client.post("/v1/images/jobs", json={"prompt": "queued behind"}).json()["job_id"]
    assert client.get(f"/v1/jobs/{second}").json()["status"] == "queued"
    assert client.post(f"/v1/jobs/{second}/cancel").json()["status"] == "cancelled"
    time.sleep(0.4)
    assert client.delete(f"/v1/jobs/{first}").status_code == 409          # still running
    client.post(f"/v1/jobs/{first}/cancel")
    assert wait_job(client, first)["status"] == "cancelled"
    assert client.delete(f"/v1/jobs/{first}").status_code == 200
    assert client.get(f"/v1/jobs/{first}").status_code == 404


def test_light_lane_not_blocked_by_heavy_job(client, monkeypatch):
    a = upload(client).json()
    monkeypatch.setattr(mock, "DELAY", 0.1)
    heavy = client.post("/v1/images/jobs", json={"prompt": "slow", "steps": 30}).json()["job_id"]
    light = wait_job(client, client.post("/v1/images/adjust", json={
        "source_asset_id": a["asset_id"], "ops": [{"op": "grayscale"}]}).json()["job_id"], timeout=3)
    assert light["status"] == "succeeded"
    assert client.get(f"/v1/jobs/{heavy}").json()["status"] in ("queued", "running")
    client.post(f"/v1/jobs/{heavy}/cancel")


def test_queue_full_returns_429(make_client, monkeypatch):
    c = make_client(queue_size=2)
    monkeypatch.setattr(mock, "DELAY", 0.5)
    codes = [c.post("/v1/images/jobs", json={"prompt": f"p{i}", "steps": 10}).status_code for i in range(5)]
    assert codes[0] == 202 and 429 in codes
    for j in c.get("/v1/jobs").json()["jobs"]:
        c.post(f"/v1/jobs/{j['job_id']}/cancel")


def test_list_jobs_newest_first(client):
    ids = [client.post("/v1/images/jobs", json={"prompt": f"n{i}"}).json()["job_id"] for i in range(3)]
    listed = [j["job_id"] for j in client.get("/v1/jobs").json()["jobs"]]
    assert listed[:3] == ids[::-1]
    assert client.get("/v1/jobs?kind=video").json()["jobs"] == []


def test_artifact_path_traversal_blocked(client):
    j = wait_job(client, client.post("/v1/images/jobs", json={"prompt": "x"}).json()["job_id"])
    store = client.app.state.store
    arts = store.get_job(j["job_id"])["artifacts"]
    arts[0]["path"] = "../studio.db"
    store.update_job(j["job_id"], artifacts=arts)
    assert client.get(j["artifacts"][0]["url"]).status_code == 404


def test_restart_recovers_interrupted_jobs(tmp_path):
    from fastapi.testclient import TestClient
    from studio.api import create_app
    from studio.storage import JobStore
    s = make_settings(tmp_path)
    store = JobStore(s.data_dir)
    store.create_job(job_id="i_old", kind="image", owner_id="local", request_hash="h", model="demo-image",
                     payload={"prompt": "left over", "steps": 2, "count": 1})
    store.update_job("i_old", status="running")
    store.conn.close()
    with TestClient(create_app(s)) as c:
        assert wait_job(c, "i_old")["status"] == "succeeded"


def test_security_headers(client):
    r = client.get("/")
    assert "script-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
