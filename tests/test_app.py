"""Endpoint-level tests for the FastAPI app (no real worker processing)."""

import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from bot.main import app, settings


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(settings.github_webhook_secret.encode(), body, hashlib.sha256).hexdigest()


def test_healthz():
    with TestClient(app) as client:
        assert client.get("/healthz").json() == {"status": "ok"}


def test_webhook_rejects_bad_signature():
    with TestClient(app) as client:
        r = client.post("/webhook", content=b"{}", headers={
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": "sha256=deadbeef",
        })
        assert r.status_code == 401


def test_webhook_ping():
    body = b"{}"
    with TestClient(app) as client:
        r = client.post("/webhook", content=body, headers={
            "X-GitHub-Event": "ping",
            "X-Hub-Signature-256": _sign(body),
        })
        assert r.status_code == 200
        assert r.text == "pong"


def test_webhook_ignores_unhandled_action():
    body = json.dumps({"action": "labeled"}).encode()
    with TestClient(app) as client:
        r = client.post("/webhook", content=body, headers={
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": _sign(body),
        })
        assert r.status_code == 204
