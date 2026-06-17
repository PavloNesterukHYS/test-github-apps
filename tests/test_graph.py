import json

from bot.graph import _extract_json


def test_extract_json_pure():
    payload = {"summary": "x", "risk_score": 0.5, "changed_functions": []}
    out = _extract_json(json.dumps(payload, indent=2))
    assert out == payload


def test_extract_json_with_banner():
    payload = {"risk_score": 1.0}
    text = "Some banner line\n" + json.dumps(payload)
    assert _extract_json(text) == payload


def test_extract_json_no_changes():
    assert _extract_json("No changes detected.") is None


def test_extract_json_garbage():
    assert _extract_json("not json at all") is None
    assert _extract_json("") is None
