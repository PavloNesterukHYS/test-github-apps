# Agentic PR Review Bot

A GitHub App that auto-reviews Pull Requests. On each PR event it runs a
**deterministic** structural analysis (via the `code-review-graph` CLI), feeds
those facts + the raw diff to an **LLM exactly once**, and posts the result
back as a PR review.

This is a **fixed pipeline, not an agent** — the steps are known in advance:

```
[GitHub] --webhook--> [FastAPI] --enqueue--> [asyncio queue] --> [worker]
                          |                                         |
                    verify HMAC + 202                               v
                                            clone -> graph -> detect-changes -> diff -> LLM -> post
```

## Layers

| Layer | Responsibility | Code |
| --- | --- | --- |
| Deterministic | what changed, blast radius, test gaps, risk score | `bot/graph.py` (wraps `code-review-graph`) |
| LLM (1 call) | *interpret & prioritise* the facts, anchor to diff lines | `bot/llm.py` (OpenAI SDK) |

The LLM never scans the repo or invents problems — `code-review-graph` does
retrieval; the LLM explains and prioritises.

## Modules

- `bot/main.py` — FastAPI app, `/webhook` endpoint, lifespan-managed worker.
- `bot/webhook.py` — HMAC-SHA256 signature verification + payload parsing.
- `bot/queue.py` — in-process `asyncio.Queue` + background worker.
- `bot/github_app.py` — App JWT → installation token, clone, diff, post review.
- `bot/graph.py` — `ensure_graph()` + `detect_changes()` over the CLI.
- `bot/llm.py` — single structured OpenAI call → two artifacts.
- `bot/pipeline.py` — wires steps 2–7, with graceful degradation.
- `bot/state.py` — `PR → last head SHA` idempotency store.

## Setup

```bash
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env   # fill in GitHub App + OpenAI credentials
```

Register a GitHub App subscribed to `pull_request` (`opened`, `synchronize`,
`reopened`), set the webhook URL to `/webhook` and the webhook secret, and
install it on the target repos.

## Run

```bash
.venv/bin/uvicorn bot.main:app --host 0.0.0.0 --port 8000
```

## Test

```bash
.venv/bin/python -m pytest          # unit + endpoint tests
```

### End-to-end without spending tokens

Set `DRY_RUN=true` in `.env`: the pipeline runs clone → graph → detect-changes
→ post, but substitutes a deterministic stub for the LLM call (summary built
from graph facts). Useful for verifying the GitHub round-trip.

## Degradation (plan §5)

- **base SHA missing / force-push** → full rebuild, review by available diff.
- **unsupported language / empty graph** → `GraphFacts.unavailable()`, diff-only review.
- **graph timeout** → caught via `CRG_TOOL_TIMEOUT`, degrade to diff-only.
- **invalid LLM JSON** → summary-only review.
- **bad inline anchors (422)** → retry per-comment, spill rejects into summary.
- worker retries each job up to 2× and never dies on a single failure.

## Idempotency (`synchronize`)

`bot/state.py` records the last processed head SHA per PR and skips duplicates
(webhook redeliveries). Before each post, the bot deletes its previous inline
comments so resyncs don't pile up stale feedback.

## Config

All tunables live in `.env` (see `.env.example`), including the
`code-review-graph` limits passed through to the CLI: `CRG_MAX_CHANGED_FUNCS`,
`CRG_MAX_TRANSITIVE_FRONTIER`, `CRG_TOOL_TIMEOUT`, `CRG_GIT_TIMEOUT`.

## Note on the old workflows

The previous GitHub Actions-based reviewer (`ai-review.yml`, `build-graph.yml`)
was moved to `.github/_disabled-workflows/` — this standalone service replaces
it. The review rubric `.github/workflow/review-guidelines.md` is retained.
