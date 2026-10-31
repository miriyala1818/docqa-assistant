"""Optional LLM answer generation with Claude (FR-04).

Used only when credentials are available (ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN, or
DOCQA_LLM=on). Any failure falls back to the local extractive generator, so the assistant
never depends on network access. Every sentence the LLM writes is still re-verified against
its cited passage by app/answer.py before it is shown.
"""
from __future__ import annotations

import json
import os

from ..config import LLM_EFFORT, LLM_MODE, LLM_MODEL

SYSTEM = """You answer employee questions using ONLY the numbered evidence passages provided.
Rules:
- Use only facts stated in the evidence. Never add outside knowledge.
- Every sentence must cite at least one passage number that directly supports it.
- Be concise: 1-4 sentences, or a short numbered list for procedures.
- If the evidence does not answer the question, set status to NOT_FOUND and return no sentences.
- If the answer depends on something the user did not say (for example their plan, product or version) and the evidence gives different answers for each, set status to NEEDS_CLARIFICATION and write one short clarifying_question that names the options found.
- If passages from different documents state incompatible facts, set status to ANSWERED_WITH_CONFLICT, state each version of the fact with its citation, and describe the disagreement in conflicts. Never silently pick one.
- Otherwise set status to ANSWERED."""

SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["ANSWERED", "ANSWERED_WITH_CONFLICT", "NEEDS_CLARIFICATION", "NOT_FOUND"]},
        "sentences": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"text": {"type": "string"}, "citations": {"type": "array", "items": {"type": "integer"}}},
                "required": ["text", "citations"],
                "additionalProperties": False,
            },
        },
        "conflicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"description": {"type": "string"},
                               "passages": {"type": "array", "items": {"type": "integer"}}},
                "required": ["description", "passages"],
                "additionalProperties": False,
            },
        },
        "clarifying_question": {"type": "string"},
    },
    "required": ["status", "sentences", "conflicts", "clarifying_question"],
    "additionalProperties": False,
}

_client = None


def available() -> bool:
    if LLM_MODE == "off":
        return False
    if LLM_MODE == "on":
        return True
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def _get_client():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic(timeout=60.0, max_retries=2)
    return _client


def generate(question: str, passages: list[dict]) -> dict:
    """passages: [{"n": 1, "label": "Doc > Section (v3.1, 2026-01-15)", "text": "..."}].
    Returns the parsed JSON dict. Raises on any API/parse problem (caller falls back)."""
    import anthropic

    evidence = "\n\n".join(f"[P{p['n']}] {p['label']}\n{p['text']}" for p in passages)
    user = f"Evidence passages:\n\n{evidence}\n\nQuestion: {question}\n\nAnswer as JSON following the rules."
    kwargs = dict(
        model=LLM_MODEL,
        max_tokens=4000,
        system=SYSTEM,
        messages=[{"role": "user", "content": user}],
        output_config={"effort": LLM_EFFORT, "format": {"type": "json_schema", "schema": SCHEMA}},
    )
    client = _get_client()
    try:
        # Server-side refusal fallback (routes a declined request to another model in the same call)
        resp = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
    except (anthropic.BadRequestError, TypeError):
        resp = client.messages.create(**kwargs)
    if resp.stop_reason == "refusal":
        raise RuntimeError("model declined the request")
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("model output was truncated")
    text = next((b.text for b in resp.content if b.type == "text"), "")
    return json.loads(text)
