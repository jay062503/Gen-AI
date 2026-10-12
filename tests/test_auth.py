import hashlib
import hmac
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi.testclient import TestClient

from conftest import make_settings, upload, wait_job
from studio.api import create_app

TOKEN = "s3cret-token-value"


def test_refuses_public_bind_without_token(tmp_path):
    with pytest.raises(RuntimeError, match="MEDIA_API_TOKEN"):
        create_app(make_settings(tmp_path, host="0.0.0.0"))
    create_app(make_settings(tmp_path, host="0.0.0.0", api_token=TOKEN))
    create_app(make_settings(tmp_path, host="0.0.0.0", allow_insecure=True))


def test_token_header_bearer_cookie(make_client):
    c = make_client(api_token=TOKEN)
    assert c.get("/health").json()["auth_required"] is True
    assert c.get("/v1/jobs").status_code == 401
    assert c.get("/v1/jobs", headers={"X-Media-Token": "wrong"}).status_code == 401
    assert c.get("/v1/jobs", headers={"X-Media-Token": TOKEN}).status_code == 200
    assert c.get("/v1/jobs", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200
    assert c.post("/v1/session", json={"token": "nope"}).status_code == 401
    assert c.post("/v1/session", json={"token": TOKEN}).status_code == 200
    assert c.get("/v1/jobs").status_code == 200            # cookie now authenticates the browser UI
    c.delete("/v1/session")
    c.cookies.clear()
    assert c.get("/v1/jobs").status_code == 401


def test_owner_isolation_for_server_to_server(make_client):
    c = make_client(api_token=TOKEN)
    alice = {"X-Media-Token": TOKEN, "X-Owner-Id": "user:alice"}
    bob = {"X-Media-Token": TOKEN, "X-Owner-Id": "user:bob"}
    jid = c.post("/v1/images/jobs", json={"prompt": "alice's"}, headers=alice).json()["job_id"]
    assert wait_job(c, jid, headers=alice)["status"] == "succeeded"
    assert c.get(f"/v1/jobs/{jid}", headers=bob).status_code == 404
    assert c.get(f"/v1/media/{jid}/0", headers=bob).status_code == 404
    assert c.get("/v1/jobs", headers=bob).json()["jobs"] == []
    aid = upload(c, headers=alice).json()["asset_id"]
    assert c.get(f"/v1/assets/{aid}", headers=bob).status_code == 404
    assert c.post("/v1/images/jobs", json={"prompt": "x", "reference_asset_ids": [aid]}, headers=bob).status_code == 422
    assert c.get("/v1/jobs", headers={"X-Media-Token": TOKEN, "X-Owner-Id": "bad owner!"}).status_code == 400


def test_owner_header_ignored_without_token_auth(client):
    jid = client.post("/v1/images/jobs", json={"prompt": "x"}, headers={"X-Owner-Id": "someone"}).json()["job_id"]
    assert client.get(f"/v1/jobs/{jid}").status_code == 200


def test_callback_rejected_unless_allow_listed(make_client):
    c = make_client()
    assert c.post("/v1/images/jobs", json={"prompt": "x", "callback_url": "http://example.com/h"}).status_code == 422
    c2 = make_client(callback_hosts=("example.com",))
    assert c2.post("/v1/images/jobs", json={"prompt": "x", "callback_url": "http://example.com/h"}).status_code == 202


def test_webhook_signature_delivered(tmp_path):
    got = {}

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            got.update(body=body, sig=self.headers.get("X-Media-Signature"))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    s = make_settings(tmp_path, api_token=TOKEN, callback_hosts=("127.0.0.1",))
    with TestClient(create_app(s)) as c:
        c.post("/v1/images/jobs", headers={"X-Media-Token": TOKEN},
               json={"prompt": "hook", "callback_url": f"http://127.0.0.1:{srv.server_port}/cb"})
        for _ in range(100):
            if got:
                break
            time.sleep(0.05)
    srv.shutdown()
    assert got, "webhook not delivered"
    expect = "sha256=" + hmac.new(TOKEN.encode(), got["body"], hashlib.sha256).hexdigest()
    assert got["sig"] == expect
    payload = json.loads(got["body"])
    assert payload["event"] == "job.succeeded" and payload["job"]["artifacts"]
