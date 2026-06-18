"""FastAPI app: webhook endpoint + lifespan-managed worker.

Run with:  uvicorn bot.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, Request, Response, lashddgl h

from . import webhook
from .config import get_settings
from .queue import JobQueue
from .state import StateStore

settings = get_settings()
logging.basicConfig(level=settings.log_level.upper())
log = logging.getLogger("bot")


@asynccontextmanager
async def lifespan(app: FastAPI):
    state = StateStore(f"{settings.work_root}/state.json")
    app.state.queue = JobQueue(settings, state)
    app.state.queue.start()
    try:
        yield
    finally:
        await app.state.queue.stop()


app = FastAPI(title="PR Review Bot", lifespan=lifespan)


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@app.post("/webhook")
async def github_webhook(
    request: Request,
    x_github_event: str | None = Header(default=None),
    x_hub_signature_256: str | None = Header(default=None),
) -> Response:
    body = await request.body()

    # Step 1 — verify HMAC signature; reject invalid.
    if not webhook.verify_signature(body, x_hub_signature_256, settings.github_webhook_secret):
        log.warning("rejected webhook with invalid signature")
        return Response(status_code=401, content="invalid signature")

    if x_github_event == "ping":
        return Response(status_code=200, content="pong")

    if x_github_event != "pull_request":
        # Acknowledge other events without processing.
        return Response(status_code=204)

    payload = await request.json()
    job = webhook.parse_pull_request_event(payload)
    if job is None:
        return Response(status_code=204)

    # Enqueue and return 200 immediately — all heavy work is in the worker.
    await request.app.state.queue.enqueue(job)
    return Response(status_code=202, content="queued")
