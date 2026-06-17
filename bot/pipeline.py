"""The fixed review pipeline executed by the worker (plan §4, steps 2–7).

token -> clone -> build graph -> detect-changes -> diff -> LLM -> post -> cleanup

Every stage degrades rather than crashes: a missing graph, an unsupported
language, or an LLM hiccup all fall back to a diff-only / summary-only review.
"""

from __future__ import annotations

import logging
import shutil
import tempfile

from . import github_app, graph, llm
from .config import Settings
from .models import GraphFacts, LLMReview, ReviewJob
from .state import StateStore

log = logging.getLogger(__name__)


def run_pipeline(job: ReviewJob, settings: Settings, state: StateStore) -> None:
    """Process one review job end-to-end. Never raises to the caller."""
    if state.already_processed(job.repo_full_name, job.pr_number, job.head_sha):
        log.info("PR %s#%s head %s already processed; skipping", job.repo_full_name, job.pr_number, job.head_sha[:8])
        return

    log.info("reviewing %s#%s (%s..%s)", job.repo_full_name, job.pr_number, job.base_sha[:8], job.head_sha[:8])

    token = github_app.get_installation_token(job.installation_id, settings)
    work_dir = tempfile.mkdtemp(prefix="pr-review-", dir=_ensure_work_root(settings))
    try:
        # Step 2 — get the code.
        github_app.clone_repo(job.repo_clone_url, token, job.head_sha, work_dir, settings)

        # Steps 3+4 — deterministic layer (degradable).
        facts = _collect_facts(work_dir, job.base_sha, settings)

        # Step 4 (cont.) — raw diff for line anchoring.
        with github_app.GitHubClient(token, settings) as gh:
            diff = gh.get_pull_diff(job.owner, job.repo, job.pr_number)

            # Step 5 — single LLM interpretation.
            result: LLMReview = llm.review(facts, diff, settings)

            # Step 6 — publish (idempotent on resync).
            gh.delete_previous_bot_comments(job.owner, job.repo, job.pr_number)
            summary = _format_summary(result, facts)
            gh.post_review(
                job.owner, job.repo, job.pr_number, job.head_sha,
                summary, result.junior_comments,
            )

        state.mark_processed(job.repo_full_name, job.pr_number, job.head_sha)
        log.info("posted review for %s#%s (%d inline comments)", job.repo_full_name, job.pr_number, len(result.junior_comments))
    finally:
        # Step 7 — cleanup work dir (clone + generated SQLite graph).
        shutil.rmtree(work_dir, ignore_errors=True)


def _collect_facts(work_dir: str, base_sha: str, settings: Settings) -> GraphFacts:
    """Build the graph and run detect-changes; degrade to empty facts on error."""
    try:
        graph.ensure_graph(work_dir, settings)
        return graph.detect_changes(work_dir, base_sha, settings)
    except graph.GraphError as exc:
        log.warning("deterministic layer unavailable, degrading to diff-only: %s", exc)
        return GraphFacts.unavailable()


def _format_summary(result: LLMReview, facts: GraphFacts) -> str:
    parts = ["🤖 **PR Review — summary для senior-рев'ювера**", ""]
    parts.append(f"**Граф знань використано:** {'так' if result.graph_used else 'ні'}")
    if facts.available:
        parts.append(f"**Risk score:** {facts.risk_score:.2f}")
        if facts.test_gaps:
            parts.append(f"**Прогалини в тестах:** {len(facts.test_gaps)}")
        if facts.affected_flows:
            parts.append(f"**Зачеплені flow:** {len(facts.affected_flows)}")
    parts.append(f"**Inline-зауважень:** {len(result.junior_comments)}")
    parts.append("")
    parts.append("## Підсумок")
    parts.append(result.senior_summary or "—")
    return "\n".join(parts)


def _ensure_work_root(settings: Settings) -> str:
    import os

    os.makedirs(settings.work_root, exist_ok=True)
    return settings.work_root
