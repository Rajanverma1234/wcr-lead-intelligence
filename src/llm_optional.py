"""OPTIONAL LLM extraction layer - OFF by default.

The rule-based pipeline is the source of truth. If the user turns the LLM on:
  1. We send ONLY this row's data and ask for strict JSON {field: {value, evidence}}.
  2. Every LLM value goes through the SAME validator as rule-based values
     (evidence must be a substring of the row; numbers in value must be in evidence).
  3. Anything that fails validation, or any API/network error -> the rule-based value is kept.
  4. Buying probability is ALWAYS computed by the rule-based scoring.

Providers (keys are read from environment / .env, never from code):
  - "anthropic": ANTHROPIC_API_KEY, model claude-haiku-5-5
      (Haiku 5.5 rejects a non-default `temperature`, so we don't send it; determinism
       comes from low effort + a strict "copy exact evidence" JSON instruction.)
  - "openai":    OPENAI_API_KEY, model from OPENAI_MODEL (default gpt-4o-mini), temperature 0
  - "ollama":    local http://localhost:11434, model from OLLAMA_MODEL (default llama3.1), temperature 0
"""
from __future__ import annotations

import json
import os
import re

from .evidence import check_field
from .extractor import CONFIDENCE, LATEST_REMARK, NA, PREVIOUS_REMARK, FieldResult
from .loader import build_source_text
from .validator import validate_field

try:  # .env support is optional
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

LLM_FIELDS = ["Budget", "Timeline", "Purpose", "Projects_Discussed",
              "Location_Preference", "Site_Visit_Status", "Objection_or_Dependency"]

SYSTEM_PROMPT = (
    "You extract real-estate lead facts. Use ONLY the lead data given. "
    "Never guess or convert currencies, and never turn vague timelines into dates. "
    'If a field is not stated, return "Not Available" with empty evidence. '
    "Evidence must be copied EXACTLY (word for word) from the remarks. "
    "The latest remark wins over the previous remark. "
    "Return ONLY a JSON object, no prose, no markdown."
)


def build_prompt(row: dict) -> str:
    keys = ["Lead_ID", "Market", "Recorded_Project", "Recorded_Budget", "Previous_Remarks", "Latest_Remarks"]
    data = {k: row.get(k, "") for k in keys}
    schema = {f: {"value": "...", "evidence": "exact quote or empty"} for f in LLM_FIELDS}
    return (
        f"Lead data:\n{json.dumps(data, ensure_ascii=False, indent=2)}\n\n"
        f"Return JSON with exactly these keys:\n{json.dumps(schema, indent=2)}\n"
        "Purpose must be Investment, End Use or Not Available. "
        "Site_Visit_Status must be one of Completed, Confirmed, Tentative, Not Now, "
        "Not Scheduled, Not Interested, Not Available."
    )


def available_providers() -> list[str]:
    providers = []
    if os.getenv("ANTHROPIC_API_KEY"):
        providers.append("anthropic")
    if os.getenv("OPENAI_API_KEY"):
        providers.append("openai")
    providers.append("ollama")  # local; may or may not be running
    return providers


def _call_anthropic(prompt: str) -> str:
    import anthropic
    client = anthropic.Anthropic(timeout=60.0)
    response = client.messages.create(
        model="claude-haiku-5-5",
        max_tokens=2000,
        output_config={"effort": "low"},
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("model refused")
    return "".join(b.text for b in response.content if b.type == "text")


def _call_openai(prompt: str) -> str:
    from openai import OpenAI
    client = OpenAI(timeout=60.0)
    response = client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        temperature=0,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content or ""


def _call_ollama(prompt: str) -> str:
    import requests
    response = requests.post(
        os.getenv("OLLAMA_URL", "http://localhost:11434") + "/api/chat",
        json={
            "model": os.getenv("OLLAMA_MODEL", "llama3.1"),
            "stream": False, "format": "json", "options": {"temperature": 0},
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}],
        },
        timeout=120,
    )
    response.raise_for_status()
    return response.json()["message"]["content"]


CALLERS = {"anthropic": _call_anthropic, "openai": _call_openai, "ollama": _call_ollama}


def parse_json(text: str) -> dict:
    """Tolerate ```json fences; take the outermost {...}."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    return json.loads(match.group(0)) if match else {}


def llm_extract_validated(row: dict, provider: str = "anthropic") -> dict:
    """Return {field: FieldResult} for LLM values that PASSED validation.

    Returns {} on any error, so the caller silently keeps rule-based values.
    """
    try:
        raw = parse_json(CALLERS[provider](build_prompt(row)))
    except Exception:
        return {}
    source_text = build_source_text(row)
    accepted = {}
    for field_name in LLM_FIELDS:
        item = raw.get(field_name) or {}
        value = str(item.get("value", NA)).strip() or NA
        evidence = str(item.get("evidence", "")).strip()
        if value == NA or not evidence:
            continue
        in_latest = evidence.lower() in row.get("Latest_Remarks", "").lower()
        source = LATEST_REMARK if in_latest else PREVIOUS_REMARK
        result = FieldResult(value, [evidence], CONFIDENCE[source], source,
                             note=f"LLM ({provider}), evidence validated")
        # same round-2 validator as the rule-based fields (evidence must be in THAT remark)
        if validate_field(result, source_text).value != NA and not check_field(row, field_name, result):
            accepted[field_name] = result
    return accepted
