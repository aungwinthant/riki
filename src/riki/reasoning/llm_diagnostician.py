from __future__ import annotations

import json
from typing import Dict, List, Optional

import httpx

from riki.models import ContractViolation, Diagnosis, Endpoint, ExecutionLog, LlmConfig

from .classifier import ViolationType
from .diagnostician import DiagnosisAction

SYSTEM_PROMPT = """You are an API contract testing diagnostician. Your job is to analyze API contract violations and determine the root cause.

Given the details of a failed API test (endpoint, method, request, response, violation), determine what action to take:

- "suggest_spec_fix": The OpenAPI spec doesn't match the actual API behavior. The spec needs updating.
- "flag_as_bug": The API server has a bug. The request was correct per the spec but the server responded incorrectly.
- "skip": This is a transient or environment issue (e.g. temporary service outage, resource temporarily missing).

Respond with a JSON object:
{
  "action": "<one of: suggest_spec_fix, flag_as_bug, skip>",
  "reason": "<brief explanation of the root cause>",
  "suggestion": "<specific suggestion for resolution>"
}"""


def _build_prompt(log: ExecutionLog, violation: ContractViolation) -> str:
    body_preview = (
        json.dumps(log.response_body, default=str)[:500]
        if log.response_body is not None
        else "N/A"
    )
    return (
        f"Endpoint: {log.method} {log.endpoint}\n"
        f"Response Status: {log.response_status}\n"
        f"Response Body: {body_preview}\n"
        f"Violation: {violation.message}\n\n"
        "Determine the root cause and suggest an action."
    )


async def llm_diagnose(
    log: ExecutionLog,
    violation: ContractViolation,
    endpoints: List[Endpoint],
    llm_config: LlmConfig,
) -> Optional[Diagnosis]:
    """Call the LLM to diagnose an UNKNOWN violation.

    Returns a Diagnosis if the LLM output passes the circuit-breaker,
    or None if it fails (caller should fall back to deterministic).
    """
    prompt = _build_prompt(log, violation)

    headers = {
        "Content-Type": "application/json",
    }
    api_key = llm_config.api_key or ""
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    base_url = (llm_config.base_url or "https://api.openai.com/v1").rstrip("/")

    payload: Dict = {
        "model": llm_config.model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        result = json.loads(content)
    except Exception:
        return None

    return _circuit_breaker(result, log, violation)


def _circuit_breaker(
    llm_output: dict,
    log: ExecutionLog,
    violation: ContractViolation,
) -> Optional[Diagnosis]:
    """Validate structured output from the LLM.

    Ensures the action maps to a valid DiagnosisAction and a reason is
    provided.  If validation fails, returns None so the caller can fall
    back to the deterministic Diagnostician.
    """
    action = (llm_output.get("action") or "").strip().lower()
    valid_actions = {a.value for a in DiagnosisAction}

    if action not in valid_actions:
        return None

    reason = (llm_output.get("reason") or "").strip()
    if not reason:
        return None

    suggestion = (llm_output.get("suggestion") or "").strip()
    if suggestion:
        reason = f"{reason}  Suggestion: {suggestion}"

    return Diagnosis(
        endpoint=violation.endpoint,
        method=violation.method,
        action=action,
        reason=reason,
        violation_type=ViolationType.UNKNOWN.value,
    )


async def llm_diagnose_all(
    log: ExecutionLog,
    violations: List[ContractViolation],
    endpoints: List[Endpoint],
    llm_config: LlmConfig,
) -> List[Diagnosis]:
    """Run LLM Diagnostician for every UNKNOWN violation.

    Each violation produces an independent LLM call.  If an individual
    call fails the circuit-breaker it is silently dropped — the caller
    keeps the deterministic Diagnosis for that violation.
    """
    results: List[Diagnosis] = []
    for v in violations:
        d = await llm_diagnose(log, v, endpoints, llm_config)
        if d is not None:
            results.append(d)
    return results
