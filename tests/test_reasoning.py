from __future__ import annotations

from typing import Any, Dict, List

import pytest

from riki.models import ContractViolation, Endpoint, ExecutionLog, HttpMethod
from riki.reasoning.classifier import ViolationType, classify
from riki.reasoning.diagnostician import DiagnosisAction, diagnose, diagnose_all
from riki.reasoning.healer import heal
from riki.reasoning.llm_diagnostician import _circuit_breaker


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def endpoints() -> List[Endpoint]:
    return [
        Endpoint(path="/pets", method=HttpMethod.POST, success_status=201),
        Endpoint(path="/pets/{petId}", method=HttpMethod.GET, success_status=200),
    ]


def _log(
    status: int,
    body: Any = None,
    method: str = "GET",
    endpoint: str = "/pets",
    headers: Dict[str, str] | None = None,
) -> ExecutionLog:
    return ExecutionLog(
        endpoint=endpoint,
        method=method,
        status="EXECUTED",
        response_status=status,
        response_body=body,
        response_headers=headers or {},
    )


def _violation(
    message: str,
    expected: int = 200,
    actual: int = 404,
    field: str | None = None,
) -> ContractViolation:
    return ContractViolation(
        endpoint="/pets",
        method="GET",
        expected_status=expected,
        actual_status=actual,
        field_path=field,
        message=message,
    )


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------

class TestClassifier:
    def test_401_is_auth_dependent(self, endpoints):
        v = _violation("Unauthorized", actual=401)
        assert classify(_log(401), v, endpoints) == ViolationType.AUTH_DEPENDENT

    def test_403_is_auth_dependent(self, endpoints):
        v = _violation("Forbidden", actual=403)
        assert classify(_log(403), v, endpoints) == ViolationType.AUTH_DEPENDENT

    def test_404_is_missing_resource(self, endpoints):
        v = _violation("Not Found", actual=404)
        assert classify(_log(404), v, endpoints) == ViolationType.MISSING_RESOURCE

    def test_200_with_schema_error(self, endpoints):
        v = _violation("Response body is not of type 'object'", expected=200, actual=200)
        assert classify(_log(200, body=[]), v, endpoints) == ViolationType.SCHEMA_ERROR

    def test_422_is_validation_error(self, endpoints):
        v = _violation("Validation failed", actual=422)
        assert classify(_log(422), v, endpoints) == ViolationType.VALIDATION_ERROR

    def test_400_with_required_is_validation(self, endpoints):
        v = _violation("'name' is a required property", actual=400)
        assert classify(_log(400), v, endpoints) == ViolationType.VALIDATION_ERROR

    def test_400_without_required_is_unknown(self, endpoints):
        v = _violation("Bad format", actual=400)
        assert classify(_log(400), v, endpoints) == ViolationType.UNKNOWN

    def test_500_is_unknown(self, endpoints):
        v = _violation("Internal Server Error", actual=500)
        assert classify(_log(500), v, endpoints) == ViolationType.UNKNOWN

    def test_503_is_unknown(self, endpoints):
        v = _violation("Service Unavailable", actual=503)
        assert classify(_log(503), v, endpoints) == ViolationType.UNKNOWN

    def test_429_is_rate_limited(self, endpoints):
        v = _violation("Too Many Requests", actual=429)
        assert classify(_log(429), v, endpoints) == ViolationType.RATE_LIMITED

    def test_503_with_retry_after_is_rate_limited(self, endpoints):
        v = _violation("Service Unavailable", actual=503)
        log = _log(503, headers={"Retry-After": "5"})
        assert classify(log, v, endpoints) == ViolationType.RATE_LIMITED

    def test_503_without_retry_after_is_still_unknown(self, endpoints):
        v = _violation("Service Unavailable", actual=503)
        assert classify(_log(503), v, endpoints) == ViolationType.UNKNOWN


# ---------------------------------------------------------------------------
# Diagnostician (deterministic)
# ---------------------------------------------------------------------------

class TestDiagnostician:
    def test_500_flagged_as_bug(self, endpoints):
        v = _violation("Internal error", actual=500)
        v.violation_type = ViolationType.UNKNOWN.value
        d = diagnose(_log(500, body={"error": "boom"}), v, endpoints)
        assert d is not None
        assert d.action == DiagnosisAction.FLAG_AS_BUG.value
        assert "500" in d.reason

    def test_503_skipped(self, endpoints):
        v = _violation("Unavailable", actual=503)
        v.violation_type = ViolationType.UNKNOWN.value
        d = diagnose(_log(503), v, endpoints)
        assert d is not None
        assert d.action == DiagnosisAction.SKIP.value

    def test_405_suggests_spec_fix(self, endpoints):
        v = _violation("Method Not Allowed", actual=405)
        v.violation_type = ViolationType.UNKNOWN.value
        d = diagnose(_log(405, method="POST"), v, endpoints)
        assert d is not None
        assert d.action == DiagnosisAction.SUGGEST_SPEC_FIX.value

    def test_200_unknown_is_spec_fix(self, endpoints):
        v = _violation("Some weird schema issue", actual=200, expected=200)
        v.violation_type = ViolationType.UNKNOWN.value
        d = diagnose(_log(200, body={}), v, endpoints)
        assert d is not None
        assert d.action == DiagnosisAction.SUGGEST_SPEC_FIX.value

    def test_skips_non_unknown_violations(self, endpoints):
        v = _violation("Not Found", actual=404)
        v.violation_type = ViolationType.MISSING_RESOURCE.value
        d = diagnose(_log(404), v, endpoints)
        assert d is None

    def test_diagnose_all_filters_unknowns(self, endpoints):
        v1 = _violation("Internal error", actual=500)
        v1.violation_type = ViolationType.UNKNOWN.value
        v2 = _violation("Not Found", actual=404)
        v2.violation_type = ViolationType.MISSING_RESOURCE.value
        results = diagnose_all(_log(500), [v1, v2], endpoints)
        assert len(results) == 1
        assert results[0].action == DiagnosisAction.FLAG_AS_BUG.value


# ---------------------------------------------------------------------------
# Healer (deterministic)
# ---------------------------------------------------------------------------

class TestHealer:
    def test_auth_dependent_returns_payload_unchanged(self):
        from riki.models import PayloadTemplate
        p = PayloadTemplate(body={"name": "test"}, query={"limit": "10"})
        result = heal(ViolationType.AUTH_DEPENDENT, p, None)
        assert result.body == p.body
        assert result.query == p.query

    def test_missing_resource_returns_payload_unchanged(self):
        from riki.models import PayloadTemplate
        p = PayloadTemplate(body={"name": "test"})
        result = heal(ViolationType.MISSING_RESOURCE, p, None)
        assert result.body == p.body

    def test_unknown_returns_payload_unchanged(self):
        from riki.models import PayloadTemplate
        p = PayloadTemplate(body={"name": "test"})
        result = heal(ViolationType.UNKNOWN, p, None)
        assert result.body == p.body

    def test_truncates_string_on_maxlength(self):
        from riki.models import PayloadTemplate
        p = PayloadTemplate(body={"name": "x" * 50})
        v = _violation("maxlength exceeded, maxLength is 10")
        result = heal(ViolationType.VALIDATION_ERROR, p, v)
        assert len(result.body["name"]) == 10  # type: ignore[index]

    def test_clamps_integer_on_minimum(self):
        from riki.models import PayloadTemplate
        p = PayloadTemplate(body={"count": 0})
        v = _violation("minimum value is 1")
        result = heal(ViolationType.VALIDATION_ERROR, p, v)
        assert result.body["count"] == 1  # type: ignore[index]

    def test_clamps_integer_on_maximum(self):
        from riki.models import PayloadTemplate
        p = PayloadTemplate(body={"count": 100})
        v = _violation("maximum value is 50")
        result = heal(ViolationType.VALIDATION_ERROR, p, v)
        assert result.body["count"] == 50  # type: ignore[index]

    def test_schema_error_returns_payload_unchanged(self):
        from riki.models import PayloadTemplate
        p = PayloadTemplate(body={"name": "test"})
        result = heal(ViolationType.SCHEMA_ERROR, p, None)
        assert result.body == p.body

    def test_rate_limited_returns_payload_unchanged(self):
        from riki.models import PayloadTemplate
        p = PayloadTemplate(body={"name": "test"})
        result = heal(ViolationType.RATE_LIMITED, p, None)
        assert result.body == p.body


# ---------------------------------------------------------------------------
# LLM Diagnostician — circuit-breaker
# ---------------------------------------------------------------------------

class TestLlmCircuitBreaker:
    def test_valid_output_accepted(self):
        d = _circuit_breaker(
            {"action": "flag_as_bug", "reason": "Server bug", "suggestion": "Fix it"},
            _log(500),
            _violation("error", actual=500),
        )
        assert d is not None
        assert d.action == "flag_as_bug"

    def test_invalid_action_dropped(self):
        d = _circuit_breaker(
            {"action": "invalid_action", "reason": "Nonsense"},
            _log(500),
            _violation("error", actual=500),
        )
        assert d is None

    def test_missing_reason_dropped(self):
        d = _circuit_breaker(
            {"action": "skip", "reason": ""},
            _log(500),
            _violation("error", actual=500),
        )
        assert d is None

    def test_case_insensitive_action_normalized(self):
        d = _circuit_breaker(
            {"action": "FLAG_AS_BUG", "reason": "Server error"},
            _log(500),
            _violation("error", actual=500),
        )
        assert d is not None
        assert d.action == "flag_as_bug"

    def test_suggestion_appended_to_reason(self):
        d = _circuit_breaker(
            {"action": "suggest_spec_fix", "reason": "Schema mismatch", "suggestion": "Check the spec"},
            _log(200),
            _violation("type error", actual=200),
        )
        assert d is not None
        assert "Suggestion:" in d.reason
