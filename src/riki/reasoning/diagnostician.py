from __future__ import annotations

from enum import Enum
from typing import List, Optional

from riki.models import ContractViolation, Diagnosis, Endpoint, ExecutionLog

from .classifier import ViolationType


class DiagnosisAction(str, Enum):
    SKIP = "skip"
    FLAG_AS_BUG = "flag_as_bug"
    SUGGEST_SPEC_FIX = "suggest_spec_fix"


def diagnose(
    log: ExecutionLog,
    violation: ContractViolation,
    endpoints: List[Endpoint],
) -> Optional[Diagnosis]:
    """Analyze an UNKNOWN violation and produce a concrete next_action.

    Pure function — fully deterministic, no side effects.
    Returns None if the violation type is not UNKNOWN.
    """
    if violation.violation_type != ViolationType.UNKNOWN.value:
        return None

    status = log.response_status or 0

    if status == 500:
        return Diagnosis(
            endpoint=violation.endpoint,
            method=violation.method,
            action=DiagnosisAction.FLAG_AS_BUG.value,
            reason="Server returned 500 Internal Server Error. This indicates a server-side bug, not a contract issue.",
            violation_type=ViolationType.UNKNOWN.value,
        )

    if status == 503:
        return Diagnosis(
            endpoint=violation.endpoint,
            method=violation.method,
            action=DiagnosisAction.SKIP.value,
            reason="Service unavailable (503). Likely transient — skip and retry on next run.",
            violation_type=ViolationType.UNKNOWN.value,
        )

    if status == 405:
        return Diagnosis(
            endpoint=violation.endpoint,
            method=violation.method,
            action=DiagnosisAction.SUGGEST_SPEC_FIX.value,
            reason=f"Method {log.method} not allowed on {violation.endpoint}. Spec may list an incorrect HTTP method.",
            violation_type=ViolationType.UNKNOWN.value,
        )

    if status == 400:
        return Diagnosis(
            endpoint=violation.endpoint,
            method=violation.method,
            action=DiagnosisAction.SUGGEST_SPEC_FIX.value,
            reason="Bad request (400). Payload format may not match server expectations. Review spec requestBody schema.",
            violation_type=ViolationType.UNKNOWN.value,
        )

    if status in (200, 201):
        return Diagnosis(
            endpoint=violation.endpoint,
            method=violation.method,
            action=DiagnosisAction.SUGGEST_SPEC_FIX.value,
            reason=f"Response schema mismatch on success (HTTP {status}). Spec response schema may be outdated or incorrect.",
            violation_type=ViolationType.UNKNOWN.value,
        )

    if status == 204:
        return Diagnosis(
            endpoint=violation.endpoint,
            method=violation.method,
            action=DiagnosisAction.SKIP.value,
            reason="Unexpected violation on 204 No Content. No response body expected.",
            violation_type=ViolationType.UNKNOWN.value,
        )

    return Diagnosis(
        endpoint=violation.endpoint,
        method=violation.method,
        action=DiagnosisAction.FLAG_AS_BUG.value,
        reason=f"Unhandled status {status} with unknown violation type. Manual investigation recommended.",
        violation_type=ViolationType.UNKNOWN.value,
    )


def diagnose_all(
    log: ExecutionLog,
    violations: List[ContractViolation],
    endpoints: List[Endpoint],
) -> List[Diagnosis]:
    """Run diagnose() for every UNKNOWN violation in the list."""
    results: List[Diagnosis] = []
    for v in violations:
        d = diagnose(log, v, endpoints)
        if d is not None:
            results.append(d)
    return results
