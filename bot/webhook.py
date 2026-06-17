"""Webhook signature verification and payload parsing."""

from __future__ import annotations

import hashlib
import hmac

from .models import ReviewJob

# PR actions we act on.
HANDLED_ACTIONS = {"opened", "synchronize", "reopened"}


def verify_signature(body: bytes, signature_header: str | None, secret: str) -> bool:
    """Constant-time HMAC-SHA256 verification of the ``X-Hub-Signature-256`` header."""
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def parse_pull_request_event(payload: dict) -> ReviewJob | None:
    """Extract a :class:`ReviewJob` from a ``pull_request`` webhook payload.

    Returns ``None`` for actions we don't handle.
    """
    action = payload.get("action")
    if action not in HANDLED_ACTIONS:
        return None

    pr = payload.get("pull_request") or {}
    repo = payload.get("repository") or {}
    installation = payload.get("installation") or {}

    return ReviewJob(
        action=action,
        installation_id=int(installation.get("id")),
        repo_full_name=repo.get("full_name", ""),
        repo_clone_url=repo.get("clone_url", ""),
        pr_number=int(pr.get("number")),
        base_sha=(pr.get("base") or {}).get("sha", ""),
        head_sha=(pr.get("head") or {}).get("sha", ""),
    )
