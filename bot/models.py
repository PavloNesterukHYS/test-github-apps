"""Internal data structures passed between pipeline stages."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ReviewJob(BaseModel):
    """A unit of work enqueued from a webhook event."""

    action: str
    installation_id: int
    repo_full_name: str  # "owner/repo"
    repo_clone_url: str
    pr_number: int
    base_sha: str
    head_sha: str

    @property
    def owner(self) -> str:
        return self.repo_full_name.split("/", 1)[0]

    @property
    def repo(self) -> str:
        return self.repo_full_name.split("/", 1)[1]


class GraphFacts(BaseModel):
    """Parsed output of ``code-review-graph detect-changes`` (full JSON).

    Field names mirror the CLI's ``analyze_changes`` return value. When the
    deterministic layer is unavailable we degrade to ``available=False`` and an
    empty fact set so the LLM layer can still review the raw diff.
    """

    available: bool = True
    summary: str = ""
    risk_score: float = 0.0
    changed_functions: list[dict[str, Any]] = Field(default_factory=list)
    affected_flows: list[dict[str, Any]] = Field(default_factory=list)
    test_gaps: list[dict[str, Any]] = Field(default_factory=list)
    review_priorities: list[dict[str, Any]] = Field(default_factory=list)
    functions_truncated: bool = False

    @classmethod
    def unavailable(cls) -> "GraphFacts":
        return cls(available=False, summary="(graph analysis unavailable — review based on diff only)")


class JuniorComment(BaseModel):
    """A single inline mentoring comment, anchored to a diff line."""

    path: str
    line: int  # line number in the NEW version (RIGHT side of the diff)
    severity: str = "nit"  # critical | major | minor | nit
    title: str
    body: str


class LLMReview(BaseModel):
    """The two artifacts produced by the single LLM call."""

    junior_comments: list[JuniorComment] = Field(default_factory=list)
    senior_summary: str = ""
    graph_used: bool = False
