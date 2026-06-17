"""Minimal persistent state: PR -> last processed head SHA.

Used for idempotency on ``synchronize`` so we don't re-review an identical
head (e.g. when GitHub redelivers a webhook). Deliberately a tiny JSON file;
swap for a real store later without changing callers.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

log = logging.getLogger(__name__)

_lock = threading.Lock()


class StateStore:
    def __init__(self, path: str):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _read(self) -> dict:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            log.warning("state file unreadable; starting empty")
            return {}

    @staticmethod
    def _key(repo_full_name: str, pr_number: int) -> str:
        return f"{repo_full_name}#{pr_number}"

    def already_processed(self, repo_full_name: str, pr_number: int, head_sha: str) -> bool:
        with _lock:
            return self._read().get(self._key(repo_full_name, pr_number)) == head_sha

    def mark_processed(self, repo_full_name: str, pr_number: int, head_sha: str) -> None:
        with _lock:
            data = self._read()
            data[self._key(repo_full_name, pr_number)] = head_sha
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.replace(self._path)
