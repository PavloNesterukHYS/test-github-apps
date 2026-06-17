import json

from bot.config import Settings
from bot.llm import _parse_response, review
from bot.models import GraphFacts


def _facts() -> GraphFacts:
    return GraphFacts(available=True, summary="2 changed funcs", risk_score=0.7, test_gaps=[{"name": "foo"}])


def test_parse_response_valid():
    raw = json.dumps({
        "junior_comments": [
            {"path": "a.py", "line": 12, "severity": "major", "title": "Bug", "body": "fix it"}
        ],
        "senior_summary": "looks risky",
    })
    out = _parse_response(raw, _facts())
    assert len(out.junior_comments) == 1
    assert out.junior_comments[0].path == "a.py"
    assert out.senior_summary == "looks risky"
    assert out.graph_used is True


def test_parse_response_invalid_json_degrades():
    out = _parse_response("{not json", _facts())
    assert out.junior_comments == []
    assert "summary" in out.senior_summary.lower() or out.senior_summary


def test_parse_response_skips_malformed_comment():
    raw = json.dumps({
        "junior_comments": [
            {"path": "a.py", "line": 1, "severity": "nit", "title": "ok", "body": "b"},
            {"path": "b.py"},  # missing required fields -> skipped
        ],
        "senior_summary": "s",
    })
    out = _parse_response(raw, _facts())
    assert len(out.junior_comments) == 1


def test_dry_run_skips_api():
    s = Settings(dry_run=True)
    out = review(_facts(), "diff text", s)
    assert out.junior_comments == []
    assert "DRY_RUN" in out.senior_summary
