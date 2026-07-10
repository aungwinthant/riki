from __future__ import annotations

import re
from typing import Any, Dict, Optional

from riki.models import ContractViolation, PayloadTemplate

from .classifier import ViolationType


def heal(
    vtype: ViolationType,
    payload: PayloadTemplate,
    violation: Optional[ContractViolation] = None,
) -> PayloadTemplate:
    """Adapt a payload based on the violation type.

    Pure function — deterministic, no side effects.
    Returns a new PayloadTemplate; original is unchanged.
    """
    if vtype == ViolationType.SCHEMA_ERROR:
        return _heal_schema_error(payload, violation)

    if vtype == ViolationType.VALIDATION_ERROR:
        return _heal_validation_error(payload, violation)

    # AUTH_DEPENDENT and MISSING_RESOURCE are handled by the planner,
    # not by payload mutation. UNKNOWN falls through unchanged.
    return payload


def _heal_schema_error(
    payload: PayloadTemplate, violation: Optional[ContractViolation]
) -> PayloadTemplate:
    """Schema errors are handled by spec override, not payload change."""
    return payload


def _heal_validation_error(
    payload: PayloadTemplate, violation: Optional[ContractViolation]
) -> PayloadTemplate:
    """Try to fix validation errors by adjusting values per the error message."""
    msg = (violation.message.lower()) if violation else ""

    new_body = _adjust_body(payload.body, msg) if payload.body else None
    new_query = _adjust_query(payload.query, msg) if payload.query else None

    return PayloadTemplate(
        body=new_body,
        query=new_query,
        path_params=payload.path_params,
    )


def _adjust_body(body: Dict[str, Any], msg: str) -> Dict[str, Any]:
    """Truncate strings that exceed maxLength, adjust numeric ranges."""
    result = dict(body)
    for key, val in result.items():
        if isinstance(val, str) and "maxlength" in msg:
            match = re.search(r"maxlength[^0-9]*(\d+)", msg)
            if match:
                max_len = int(match.group(1))
                if len(val) > max_len:
                    result[key] = val[:max_len]
        if isinstance(val, int) and "minimum" in msg:
            match = re.search(r"minimum[^0-9]*(-?\d+)", msg)
            if match:
                minimum = int(match.group(1))
                if val < minimum:
                    result[key] = minimum
        if isinstance(val, int) and "maximum" in msg:
            match = re.search(r"maximum[^0-9]*(\d+)", msg)
            if match:
                maximum = int(match.group(1))
                if val > maximum:
                    result[key] = maximum
    return result


def _adjust_query(query: Dict[str, str], msg: str) -> Dict[str, str]:
    """Apply the same truncation/ranging logic to query params."""
    result = dict(query)
    for key, val in result.items():
        if "maxlength" in msg:
            match = re.search(r"maxlength[^0-9]*(\d+)", msg)
            if match:
                max_len = int(match.group(1))
                if len(val) > max_len:
                    result[key] = val[:max_len]
    return result
