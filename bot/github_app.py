"""GitHub App authentication and REST interactions.

Responsibilities:
  - mint a short-lived installation access token from the App credentials;
  - clone a repo at the PR head (with full history so both base and head
    SHAs are present for ``detect-changes``);
  - fetch the PR diff;
  - post a review (inline comments + summary body);
  - support idempotency by dismissing the bot's previous reviews.
"""

from __future__ import annotations

import logging
import subprocess
import time

import httpx
import jwt

from .config import Settings
from .models import JuniorComment

log = logging.getLogger(__name__)

_API_VERSION = "2022-11-28"


class GitHubClient:
    """Thin REST client scoped to a single installation token."""

    def __init__(self, token: str, settings: Settings):
        self._token = token
        self._base = settings.github_api_url.rstrip("/")
        self._http = httpx.Client(
            timeout=30.0,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": _API_VERSION,
                "User-Agent": "pr-review-bot",
            },
        )

    # ── lifecycle ─────────────────────────────────────────────────────────
    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "GitHubClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ── data fetch ────────────────────────────────────────────────────────
    def get_pull_diff(self, owner: str, repo: str, number: int) -> str:
        """Return the unified diff of the PR (the ``.diff`` media type)."""
        resp = self._http.get(
            f"{self._base}/repos/{owner}/{repo}/pulls/{number}",
            headers={"Accept": "application/vnd.github.v3.diff"},
        )
        resp.raise_for_status()
        return resp.text

    # ── posting ───────────────────────────────────────────────────────────
    def post_review(
        self,
        owner: str,
        repo: str,
        number: int,
        commit_sha: str,
        summary_body: str,
        comments: list[JuniorComment],
    ) -> dict:
        """Create a single PR review with inline comments + a summary body.

        Inline comments that GitHub rejects (e.g. the line is outside the
        diff) are dropped from the inline set and folded into the summary so
        the feedback is never silently lost.
        """
        inline = [
            {
                "path": c.path,
                "line": c.line,
                "side": "RIGHT",
                "body": f"**[{c.severity}] {c.title}**\n\n{c.body}",
            }
            for c in comments
        ]
        review = {
            "commit_id": commit_sha,
            "body": summary_body,
            "event": "COMMENT",
            "comments": inline,
        }
        resp = self._http.post(
            f"{self._base}/repos/{owner}/{repo}/pulls/{number}/reviews",
            json=review,
        )
        if resp.status_code == 422 and inline:
            # Some inline anchors were invalid; retry comment-by-comment and
            # spill the rejects into the summary.
            log.warning("review 422 with %d inline comments; retrying individually", len(inline))
            return self._post_review_resilient(owner, repo, number, commit_sha, summary_body, comments)
        resp.raise_for_status()
        return resp.json()

    def _post_review_resilient(
        self, owner, repo, number, commit_sha, summary_body, comments
    ) -> dict:
        accepted, leftover = [], []
        for c in comments:
            payload = {
                "path": c.path,
                "line": c.line,
                "side": "RIGHT",
                "body": f"**[{c.severity}] {c.title}**\n\n{c.body}",
            }
            probe = self._http.post(
                f"{self._base}/repos/{owner}/{repo}/pulls/{number}/reviews",
                json={"commit_id": commit_sha, "event": "COMMENT", "comments": [payload], "body": ""},
            )
            if probe.status_code < 300:
                accepted.append(c)
            else:
                leftover.append(c)
        body = summary_body
        if leftover:
            body += "\n\n## Зауваження поза diff (inline не пройшов)\n"
            for c in leftover:
                body += f"- **[{c.severity}]** `{c.path}:{c.line}` — {c.title}: {c.body}\n"
        resp = self._http.post(
            f"{self._base}/repos/{owner}/{repo}/pulls/{number}/reviews",
            json={"commit_id": commit_sha, "body": body, "event": "COMMENT"},
        )
        resp.raise_for_status()
        return resp.json()

    def delete_previous_bot_comments(self, owner: str, repo: str, number: int) -> int:
        """Delete this bot's prior inline review comments (idempotency on resync).

        ``COMMENT`` reviews cannot be "dismissed", so to avoid piling up stale
        inline comments on each ``synchronize`` we delete the bot-authored
        review comments before posting the fresh review. Best-effort: errors
        are logged, not raised. Returns the count deleted.
        """
        try:
            resp = self._http.get(
                f"{self._base}/repos/{owner}/{repo}/pulls/{number}/comments",
                params={"per_page": 100},
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("could not list review comments for cleanup: %s", exc)
            return 0

        deleted = 0
        for c in resp.json():
            if (c.get("user") or {}).get("type") != "Bot":
                continue
            try:
                self._http.delete(
                    f"{self._base}/repos/{owner}/{repo}/pulls/comments/{c['id']}"
                )
                deleted += 1
            except httpx.HTTPError as exc:
                log.warning("failed to delete comment %s: %s", c.get("id"), exc)
        return deleted


# ── App-level auth (no installation context yet) ───────────────────────────
def _app_jwt(settings: Settings) -> str:
    """Build a short-lived (10 min) App JWT signed with the private key."""
    now = int(time.time())
    payload = {"iat": now - 60, "exp": now + 9 * 60, "iss": settings.github_app_id}
    return jwt.encode(payload, settings.resolve_private_key(), algorithm="RS256")


def get_installation_token(installation_id: int, settings: Settings) -> str:
    """Exchange the App JWT for a short-lived installation access token."""
    app_jwt = _app_jwt(settings)
    url = f"{settings.github_api_url.rstrip('/')}/app/installations/{installation_id}/access_tokens"
    resp = httpx.post(
        url,
        headers={
            "Authorization": f"Bearer {app_jwt}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": _API_VERSION,
            "User-Agent": "pr-review-bot",
        },
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()["token"]


def clone_repo(clone_url: str, token: str, head_sha: str, dest: str, settings: Settings) -> None:
    """Full clone of the repo with the installation token, checked out at head.

    A full clone (not shallow) is required so both base and head SHAs are
    present in history for ``detect-changes --base``.
    """
    authed = clone_url.replace("https://", f"https://x-access-token:{token}@", 1)
    subprocess.run(
        ["git", "clone", authed, dest],
        check=True,
        capture_output=True,
        text=True,
        timeout=settings.crg_git_timeout * 5,
    )
    # Best-effort checkout of head; the clone default branch may differ.
    subprocess.run(
        ["git", "-C", dest, "checkout", head_sha],
        check=False,
        capture_output=True,
        text=True,
        timeout=settings.crg_git_timeout,
    )
