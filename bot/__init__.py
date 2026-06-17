"""Agentic-style (fixed-pipeline) PR review bot.

A GitHub App that, on each PR event, runs a deterministic structural analysis
(via the ``code-review-graph`` CLI), feeds the collected facts plus the raw
diff to an LLM exactly once, and posts the result back as a PR review.

The pipeline is intentionally *not* an agent: the sequence of steps is fixed
(event -> code -> graph -> facts -> LLM -> comments).
"""

__all__ = ["__version__"]
__version__ = "0.1.0"
