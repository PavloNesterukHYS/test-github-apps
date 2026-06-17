"""In-process async job queue + worker (plan: queue mechanism = in-process).

A single ``asyncio.Queue`` fed by the webhook handler and drained by a
background worker task. The pipeline itself is synchronous (subprocess + sync
HTTP), so it runs in a thread executor to avoid blocking the event loop.

This is intentionally the simplest thing that satisfies "heavy work happens in
the worker, the webhook returns 200 immediately". The interface (``enqueue``)
is stable, so a Redis/RQ-backed queue can replace it later.
"""

from __future__ import annotations

import asyncio
import logging

from . import pipeline
from .config import Settings
from .models import ReviewJob
from .state import StateStore

log = logging.getLogger(__name__)

_MAX_ATTEMPTS = 2


class JobQueue:
    def __init__(self, settings: Settings, state: StateStore):
        self._settings = settings
        self._state = state
        self._queue: asyncio.Queue[ReviewJob] = asyncio.Queue()
        self._worker: asyncio.Task | None = None

    def start(self) -> None:
        if self._worker is None:
            self._worker = asyncio.create_task(self._run(), name="review-worker")
            log.info("review worker started")

    async def stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
            self._worker = None

    async def enqueue(self, job: ReviewJob) -> None:
        await self._queue.put(job)
        log.info("enqueued %s#%s (qsize=%d)", job.repo_full_name, job.pr_number, self._queue.qsize())

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            job = await self._queue.get()
            for attempt in range(1, _MAX_ATTEMPTS + 1):
                try:
                    await loop.run_in_executor(
                        None, pipeline.run_pipeline, job, self._settings, self._state
                    )
                    break
                except Exception:  # noqa: BLE001 — keep the worker alive on any failure
                    log.exception("pipeline failed for %s#%s (attempt %d/%d)",
                                  job.repo_full_name, job.pr_number, attempt, _MAX_ATTEMPTS)
            self._queue.task_done()
