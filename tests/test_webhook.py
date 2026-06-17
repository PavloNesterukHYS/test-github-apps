import hashlib
import hmac
import json

from bot import webhook


def _sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_verify_signature_valid():
    body = b'{"hello":"world"}'
    sig = _sign(body, "s3cret")
    assert webhook.verify_signature(body, sig, "s3cret") is True


def test_verify_signature_invalid_and_missing():
    body = b"{}"
    assert webhook.verify_signature(body, _sign(body, "wrong"), "right") is False
    assert webhook.verify_signature(body, None, "right") is False
    assert webhook.verify_signature(body, "md5=abc", "right") is False


def test_parse_pull_request_event_handled():
    payload = {
        "action": "opened",
        "installation": {"id": 42},
        "repository": {"full_name": "acme/widgets", "clone_url": "https://github.com/acme/widgets.git"},
        "pull_request": {"number": 7, "base": {"sha": "base123"}, "head": {"sha": "head456"}},
    }
    job = webhook.parse_pull_request_event(payload)
    assert job is not None
    assert job.owner == "acme" and job.repo == "widgets"
    assert job.pr_number == 7
    assert job.base_sha == "base123" and job.head_sha == "head456"
    assert job.installation_id == 42


def test_parse_pull_request_event_unhandled_action():
    assert webhook.parse_pull_request_event({"action": "labeled"}) is None
