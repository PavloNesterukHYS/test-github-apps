"""LLM layer — called exactly once per PR.

The LLM does NOT search the repo or hunt for problems. The deterministic layer
(:mod:`bot.graph`) has already gathered the structural facts; the LLM's job is
to *interpret and prioritise* them and tie feedback to concrete diff lines.

Produces two artifacts in a single pass:
  - ``junior_comments``: mentoring feedback anchored to file+line;
  - ``senior_summary``: a concise risk-oriented summary for a senior reviewer.
"""

from __future__ import annotations

import json
import logging

from .config import Settings
from .models import GraphFacts, JuniorComment, LLMReview

log = logging.getLogger(__name__)

# Cap how much diff we send to keep the single call bounded.
_MAX_DIFF_CHARS = 60_000

_SYSTEM_PROMPT = (
    "Ти — досвідчений, конструктивний код-рев'ювер. Тобі вже передали ГОТОВІ "
    "детерміновані факти про зміни (граф залежностей, blast radius, прогалини "
    "в тестах, risk score) та сам diff. Твоє завдання — НЕ шукати проблеми з "
    "нуля, а ПОЯСНИТИ і ПРІОРИТИЗУВАТИ вже зібрані факти, прив'язавши їх до "
    "конкретних рядків diff. Якщо граф-факти відсутні — рев'ю роби по самому "
    "diff. Відповідай ВИКЛЮЧНО валідним JSON за наданою схемою. Мова відповіді "
    "— українська."
)

# JSON schema the model must follow (used as response_format json_schema).
_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "junior_comments": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "path": {"type": "string"},
                    "line": {"type": "integer"},
                    "severity": {"type": "string", "enum": ["critical", "major", "minor", "nit"]},
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["path", "line", "severity", "title", "body"],
            },
        },
        "senior_summary": {"type": "string"},
    },
    "required": ["junior_comments", "senior_summary"],
}


def _build_user_prompt(facts: GraphFacts, diff: str) -> str:
    facts_json = json.dumps(
        {
            "available": facts.available,
            "summary": facts.summary,
            "risk_score": facts.risk_score,
            "changed_functions": facts.changed_functions,
            "affected_flows": facts.affected_flows,
            "test_gaps": facts.test_gaps,
            "review_priorities": facts.review_priorities,
            "functions_truncated": facts.functions_truncated,
        },
        ensure_ascii=False,
        default=str,
    )
    truncated_diff = diff[:_MAX_DIFF_CHARS]
    diff_note = "" if len(diff) <= _MAX_DIFF_CHARS else "\n(diff обрізано через розмір)\n"
    return (
        "Ось ДЕТЕРМІНОВАНІ факти від графа знань (JSON):\n"
        f"```json\n{facts_json}\n```\n\n"
        "Ось сам diff PR (рядки + позначені зміни):\n"
        f"```diff\n{truncated_diff}\n```{diff_note}\n\n"
        "Згенеруй ДВА артефакти за один прохід:\n"
        "1) junior_comments[]: менторський фідбек на конкретні рядки diff — що "
        "не так, ЧОМУ, що може зламатись (бери з blast radius / affected_flows), "
        "як виправити. line — номер рядка в НОВІЙ версії файла (права сторона diff).\n"
        "2) senior_summary: стисле summary — risk score, зачеплені flow, "
        "прогалини в тестах, на що звернути увагу.\n"
        "Поясни і пріоритизуй факти, НЕ вигадуй проблем поза наданими фактами/diff."
    )


def review(facts: GraphFacts, diff: str, settings: Settings) -> LLMReview:
    """Run the single LLM call and parse the structured response.

    On ``settings.dry_run`` returns a deterministic stub (no API call). On any
    parse/API failure, degrades to a summary-only review rather than raising.
    """
    if settings.dry_run:
        return _stub_review(facts)

    user_prompt = _build_user_prompt(facts, diff)
    try:
        raw = _call_openai(user_prompt, settings)
    except Exception as exc:  # noqa: BLE001 — network/SDK errors are non-fatal
        log.exception("LLM call failed: %s", exc)
        return LLMReview(
            junior_comments=[],
            senior_summary=_fallback_summary(facts, note="LLM-виклик не вдався — summary з графа."),
            graph_used=facts.available,
        )

    return _parse_response(raw, facts)


def _call_openai(user_prompt: str, settings: Settings) -> str:
    from openai import OpenAI

    client = OpenAI(api_key=settings.openai_api_key)
    resp = client.chat.completions.create(
        model=settings.llm_model,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "pr_review", "strict": True, "schema": _RESPONSE_SCHEMA},
        },
        temperature=0.2,
    )
    return resp.choices[0].message.content or ""


def _parse_response(raw: str, facts: GraphFacts) -> LLMReview:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("LLM returned invalid JSON; falling back to summary-only")
        return LLMReview(
            junior_comments=[],
            senior_summary=_fallback_summary(facts, note="LLM повернув невалідний JSON."),
            graph_used=facts.available,
        )

    comments: list[JuniorComment] = []
    for item in data.get("junior_comments", []) or []:
        try:
            comments.append(JuniorComment(**item))
        except Exception:  # noqa: BLE001 — skip malformed individual comments
            log.debug("skipping malformed comment: %r", item)
    return LLMReview(
        junior_comments=comments,
        senior_summary=data.get("senior_summary", "") or _fallback_summary(facts),
        graph_used=facts.available,
    )


def _fallback_summary(facts: GraphFacts, note: str = "") -> str:
    head = "🤖 **PR Review (summary)**\n\n"
    if note:
        head += f"> {note}\n\n"
    if facts.available:
        head += facts.summary or "(граф не повернув деталей)"
    else:
        head += "Граф-аналіз недоступний — рев'ю обмежене самим diff."
    return head


def _stub_review(facts: GraphFacts) -> LLMReview:
    """Deterministic offline review for DRY_RUN end-to-end testing."""
    return LLMReview(
        junior_comments=[],
        senior_summary=_fallback_summary(facts, note="DRY_RUN: LLM не викликався."),
        graph_used=facts.available,
    )
