from __future__ import annotations

from enum import Enum
from typing import Any, List, Optional

from riki.models import ContractViolation, Endpoint, ExecutionLog


class ViolationType(str, Enum):
    AUTH_DEPENDENT = "auth_dependent"
    MISSING_RESOURCE = "missing_resource"
    SCHEMA_ERROR = "schema_error"
    VALIDATION_ERROR = "validation_error"
    RATE_LIMITED = "rate_limited"
    UNKNOWN = "unknown"


def classify(
    log: ExecutionLog,
    violation: ContractViolation,
    endpoints: List[Endpoint],
) -> ViolationType:
    """Classify a contract violation into a type for downstream handling.

    Pure function — no side effects, fully deterministic.
    """
    status = log.response_status or 0

    # 401 → auth dependent (credential missing or wrong)
    if status == 401:
        return ViolationType.AUTH_DEPENDENT

    # 403 → also auth dependent (authenticated but not authorized)
    if status == 403:
        return ViolationType.AUTH_DEPENDENT

    # 404 → missing resource (param value not found)
    if status == 404:
        return ViolationType.MISSING_RESOURCE

    # 429 → rate limited
    if status == 429:
        return ViolationType.RATE_LIMITED

    # 503 with Retry-After header → rate limited (not transient server error)
    if status == 503 and _has_retry_after(log):
        return ViolationType.RATE_LIMITED

    # 200/201 with schema violation → response shape mismatch
    if status in (200, 201) and _is_schema_violation(violation):
        return ViolationType.SCHEMA_ERROR

    # 422 on request body → validation error
    if status == 422:
        return ViolationType.VALIDATION_ERROR

    # 400 could be validation or missing data
    if status == 400:
        if "required" in violation.message.lower():
            return ViolationType.VALIDATION_ERROR
        return ViolationType.UNKNOWN

    # Anything else doesn't fit known buckets
    return ViolationType.UNKNOWN


def _is_schema_violation(violation: ContractViolation) -> bool:
    """Heuristic: schema violations from openapi-core mention type or property mismatches."""
    msg = violation.message.lower()
    keywords = ["type", "property", "required", "additional properties", 
                 "schema", "validation", "is not", "expected"]
    return any(k in msg for k in keywords)


def _has_retry_after(log: ExecutionLog) -> bool:
    """Check if response headers contain a Retry-After header."""
    headers = getattr(log, "response_headers", {}) or {}
    val = headers.get("retry-after") or headers.get("Retry-After") or ""
    return bool(val)
