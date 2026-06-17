"""Wrapper around the ``code-review-graph`` CLI — the deterministic layer.

This module is the *only* place that knows how to invoke the tool and parse
its output. It is deliberately small and self-contained so a caching layer
can be slotted in later behind :func:`ensure_graph` without touching callers.

Verified against code-review-graph 2.3.6:
  - ``build`` parses the whole repo into ``.code-review-graph/graph.db``.
  - ``detect-changes --base <sha>`` (without ``--brief``) prints the full
    result as ``json.dumps(result, indent=2)`` to stdout. When there are no
    changes it prints the literal text ``No changes detected.`` instead.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys

from .config import Settings
from .models import GraphFacts

log = logging.getLogger(__name__)


class GraphError(RuntimeError):
    """Raised when the deterministic layer cannot produce usable facts.

    Callers should treat this as non-fatal and degrade to diff-only review.
    """


def _cli() -> list[str]:
    """Resolve how to invoke the CLI (console script, else ``python -m``)."""
    exe = shutil.which("code-review-graph")
    if exe:
        return [exe]
    return [sys.executable, "-m", "code_review_graph"]


def _run(args: list[str], *, cwd: str, settings: Settings, timeout: int) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, **settings.crg_env()}
    cmd = [*_cli(), *args]
    log.debug("crg: %s (cwd=%s)", " ".join(args), cwd)
    return subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def ensure_graph(repo_dir: str, settings: Settings) -> None:
    """Build the knowledge graph for ``repo_dir`` from scratch.

    Caching between runs is intentionally out of scope (see plan §3/§8); this
    always rebuilds. Raises :class:`GraphError` on failure or timeout so the
    pipeline can degrade gracefully.
    """
    try:
        proc = _run(["build"], cwd=repo_dir, settings=settings, timeout=settings.crg_tool_timeout)
    except subprocess.TimeoutExpired as exc:
        raise GraphError(f"graph build timed out after {settings.crg_tool_timeout}s") from exc
    if proc.returncode != 0:
        raise GraphError(f"graph build failed (rc={proc.returncode}): {proc.stderr.strip()[:500]}")
    log.info("graph built for %s", repo_dir)


def detect_changes(repo_dir: str, base_sha: str, settings: Settings) -> GraphFacts:
    """Run ``detect-changes --base <base_sha>`` and parse the JSON facts.

    Returns :class:`GraphFacts` with ``available=False`` when the tool reports
    no changes or yields no parsable JSON (e.g. unsupported language). Raises
    :class:`GraphError` only on hard failures (non-zero exit, timeout).
    """
    try:
        proc = _run(
            ["detect-changes", "--base", base_sha],
            cwd=repo_dir,
            settings=settings,
            timeout=settings.crg_tool_timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise GraphError(f"detect-changes timed out after {settings.crg_tool_timeout}s") from exc
    if proc.returncode != 0:
        raise GraphError(f"detect-changes failed (rc={proc.returncode}): {proc.stderr.strip()[:500]}")

    data = _extract_json(proc.stdout)
    if data is None:
        log.warning("detect-changes produced no JSON (stdout=%r)", proc.stdout.strip()[:200])
        return GraphFacts.unavailable()

    return GraphFacts(
        available=True,
        summary=data.get("summary", ""),
        risk_score=float(data.get("risk_score", 0.0) or 0.0),
        changed_functions=data.get("changed_functions", []) or [],
        affected_flows=data.get("affected_flows", []) or [],
        test_gaps=data.get("test_gaps", []) or [],
        review_priorities=data.get("review_priorities", []) or [],
        functions_truncated=bool(data.get("functions_truncated", False)),
    )


def _extract_json(stdout: str) -> dict | None:
    """Pull the JSON object out of CLI stdout.

    The CLI prints pure ``json.dumps`` on success, but we defensively slice
    from the first ``{`` to the last ``}`` in case any banner sneaks in.
    """
    text = stdout.strip()
    if not text or text.startswith("No changes detected"):
        return None
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None
