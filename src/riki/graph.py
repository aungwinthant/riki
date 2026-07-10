from __future__ import annotations

import time
from typing import Any, Dict, List

from langgraph.graph import END, StateGraph

from .models import (
    AuthScheme,
    ContractViolation,
    Endpoint,
    ExecutionLog,
    LlmConfig,
    PayloadTemplate,
    TestState,
)
from .reasoning.classifier import classify, ViolationType
from .reasoning.diagnostician import diagnose_all
from .reasoning.flow import (
    execute_auth_flow,
    extract_token_from_response,
    find_auth_flow,
    build_bearer_scheme,
)
from .reasoning.healer import heal as heal_payload_via_classifier
from .reasoning.llm_diagnostician import llm_diagnose_all as llm_diagnose_all_violations
from .tools import (
    build_request_schema,
    detect_auth_schemes,
    execute_http_request,
    extract_endpoints,
    extract_memory_variables,
    generate_payload,
    load_spec,
    parse_endpoint_key,
    topological_sort,
    validate_response,
)

MAX_RETRIES = 2


def _ensure_state(raw: Dict[str, Any]) -> TestState:
    if isinstance(raw, TestState):
        return raw
    return TestState(**raw)


def plan_sequence(state: TestState) -> Dict[str, Any]:
    s = _ensure_state(state) if isinstance(state, dict) else state
    raw = load_spec(s.spec_path)
    endpoints = extract_endpoints(raw)
    planner = topological_sort(endpoints)

    detected = detect_auth_schemes(raw)
    merged_auth: List[Dict[str, Any]] = []
    seen_types = set()
    for d in detected:
        seen_types.add(d.type)
        merged_auth.append(d.model_dump())
    for a in s.auth:
        if a.type not in seen_types:
            seen_types.add(a.type)
            merged_auth.append(a.model_dump())

    # Phase 1.1: Auth flow — proactively exchange basic creds for bearer token
    auth_objs = [AuthScheme(**a) for a in merged_auth]
    login_key = find_auth_flow(endpoints, auth_objs)
    if login_key:
        basic_auth = next((a for a in auth_objs if a.type == "basic" and a.username), None)
        if basic_auth:
            token_response = execute_auth_flow(s.base_url, login_key, basic_auth)
            if token_response:
                token = extract_token_from_response(token_response)
                if token:
                    bearer = build_bearer_scheme(token)
                    merged_auth = [a for a in merged_auth if a.get("type") != "bearer"]
                    merged_auth.append(bearer.model_dump())
                    planner.ordered_keys = [k for k in planner.ordered_keys if k != login_key]

    return {
        "raw_spec": raw,
        "endpoints": [ep.model_dump() for ep in endpoints],
        "endpoint_queue": planner.ordered_keys,
        "auth": merged_auth,
        "start_time": time.time(),
    }


def _get_current_ep(state: TestState) -> str:
    if state.current_endpoint:
        return state.current_endpoint
    if state.endpoint_queue:
        return state.endpoint_queue[0]
    return ""


def generate_payload_node(state: TestState) -> Dict[str, Any]:
    s = _ensure_state(state) if isinstance(state, dict) else state
    current_key = _get_current_ep(s)
    if not current_key:
        return {"error": "No endpoint to process"}

    method, path = parse_endpoint_key(current_key)
    body_schema, query_schema, path_schema = build_request_schema(
        s.raw_spec, method, path
    )

    payload = generate_payload(
        body_schema, query_schema, path_schema, s.raw_spec, s.memory, current_key
    )

    merged_payloads = dict(s.payloads)
    merged_payloads[current_key] = payload.model_dump()
    return {"current_endpoint": current_key, "payloads": merged_payloads}


async def execute_request(state: TestState) -> Dict[str, Any]:
    s = _ensure_state(state) if isinstance(state, dict) else state
    current_key = s.current_endpoint
    if not current_key:
        return {"error": "No current endpoint set", "current_endpoint": None}

    method, path = parse_endpoint_key(current_key)
    raw_payload = s.payloads.get(current_key, {})
    payload = PayloadTemplate(**raw_payload) if isinstance(raw_payload, dict) else raw_payload

    start = time.time()
    auth_objs = [
        a if isinstance(a, AuthScheme) else AuthScheme(**a) for a in getattr(s, "auth", [])
    ]
    status_code, headers, body = await execute_http_request(
        s.base_url, method, path, payload, s.memory, auth=auth_objs
    )
    duration = (time.time() - start) * 1000

    log = ExecutionLog(
        endpoint=path,
        method=method,
        status="EXECUTED",
        request_payload=payload.model_dump(exclude_none=True),
        response_status=status_code,
        response_body=body,
        duration_ms=duration,
    )

    merged_results = dict(s.results)
    merged_results[current_key] = log.model_dump()
    return {"results": merged_results, "current_endpoint": current_key}


def _patch_array_schema(
    raw_spec: Dict[str, Any], method: str, path: str, response_body: Any
) -> Optional[Dict[str, Any]]:
    """If spec says type:object but response is a list, patch the schema.

    Returns the override dict if applied, None otherwise.
    The override MUST be reported — never applied silently.
    """
    if not isinstance(response_body, list):
        return None
    paths = raw_spec.get("paths", {})
    methods = paths.get(path, {})
    details = methods.get(method.lower(), {})
    resp = details.get("responses", {}).get("200", {})
    content = resp.get("content", {})
    schema = content.get("application/json", {}).get("schema", {})
    if schema.get("type") == "array":
        return None
    override = {"type": "array", "items": {"type": "object"}}
    if "paths" not in raw_spec:
        return override
    if path not in raw_spec["paths"]:
        raw_spec["paths"][path] = {}
    if method.lower() not in raw_spec["paths"][path]:
        raw_spec["paths"][path][method.lower()] = {}
    resp_path = raw_spec["paths"][path][method.lower()].setdefault("responses", {}).setdefault("200", {}).setdefault("content", {}).setdefault("application/json", {}).setdefault("schema", override)
    return override


async def validate_contract(state: TestState) -> Dict[str, Any]:
    s = _ensure_state(state) if isinstance(state, dict) else state
    current_key = s.current_endpoint
    if not current_key:
        return {"error": "No current endpoint set"}

    raw_log = s.results.get(current_key, {})
    log = ExecutionLog(**raw_log) if isinstance(raw_log, dict) else raw_log

    method, path = parse_endpoint_key(current_key)
    ep = _find_endpoint(s.endpoints, method, path)

    if log.response_status is not None and ep and ep.success_status:
        if log.response_status != ep.success_status:
            violation = ContractViolation(
                endpoint=path,
                method=method,
                expected_status=ep.success_status,
                actual_status=log.response_status,
                field_path=None,
                message=f"Expected status {ep.success_status}, got {log.response_status}",
            )
            log.violations.append(violation)

    schema_violations = validate_response(
        s.raw_spec,
        method,
        path,
        log.response_status or 0,
        {"content-type": "application/json"},
        log.response_body,
    )

    # Phase 1.3: Schema override — if response is array but spec says object,
    # patch the spec and flag it. Never silent.
    schema_override = None
    if schema_violations and isinstance(log.response_body, list) and log.response_status in (200, 201):
        schema_override = _patch_array_schema(s.raw_spec, method, path, log.response_body)
        if schema_override:
            schema_violations = []

    # Phase 2: Classify each violation
    for v in log.violations:
        vtype = classify(log, v, s.endpoints)
        v.violation_type = vtype.value

    log.violations.extend(schema_violations)
    for v in log.violations:
        if not v.violation_type:
            vtype = classify(log, v, s.endpoints)
            v.violation_type = vtype.value

    # Phase 4: Diagnostician — analyze UNKNOWN violations
    unknown_violations = [v for v in log.violations if v.violation_type == ViolationType.UNKNOWN.value]
    new_diagnoses = diagnose_all(log, unknown_violations, s.endpoints)
    merged_diagnoses = list(getattr(s, "diagnoses", []))
    merged_diagnoses.extend(new_diagnoses)

    # Phase 5: LLM Diagnostician (opt-in) — run on same UNKNOWN violations
    llm_diags: List = []
    llm_config = getattr(s, "llm_config", None)
    if llm_config is not None and unknown_violations:
        llm_diags = await llm_diagnose_all_violations(log, unknown_violations, s.endpoints, llm_config)
        for ld in llm_diags:
            merged_diagnoses.append(ld)

    merged_spec_overrides = dict(getattr(s, "spec_overrides", {}))
    if schema_override:
        override_key = f"{method}:{path}"
        merged_spec_overrides[override_key] = {
            "field": "responses.200.content.application/json.schema",
            "from_type": "object",
            "to_type": "array",
        }

    merged_results = dict(s.results)
    if log.violations:
        log.status = "FAIL"
        log_dict = log.model_dump()
        log_dict["violations"] = [v.model_dump() for v in log.violations]
        merged_results[current_key] = log_dict

        reasoning_step = {
            "step": len(s.reasoning_log) + 1,
            "endpoint": current_key,
            "action": "classified",
            "observation": f"Response status {log.response_status} with {len(log.violations)} violation(s)",
            "decision": "heal" if _should_heal(log.violations) else "advance",
            "explanation": _build_explanation(log.violations),
        }
        merged_log = list(s.reasoning_log)
        merged_log.append(reasoning_step)

        if new_diagnoses or llm_diags:
            source = "llm" if llm_diags else "deterministic"
            all_diags = llm_diags if llm_diags else new_diagnoses
            diag_step = {
                "step": len(merged_log) + 1,
                "endpoint": current_key,
                "action": "diagnosed",
                "observation": f"{len(all_diags)} UNKNOWN violation(s) analyzed via {source}",
                "decision": all_diags[0].action,
                "explanation": all_diags[0].reason,
            }
            merged_log.append(diag_step)

        return {
            "results": merged_results,
            "violations": [v.model_dump() for v in schema_violations],
            "current_endpoint": current_key,
            "spec_overrides": merged_spec_overrides,
            "reasoning_log": merged_log,
            "diagnoses": [d.model_dump() for d in merged_diagnoses],
        }
    else:
        log.status = "PASS"
        updated_memory = extract_memory_variables(
            log.response_body, {}, s.memory, path=path
        )
        merged_results[current_key] = log.model_dump()
        return {
            "results": merged_results,
            "memory": updated_memory,
            "current_endpoint": current_key,
            "spec_overrides": merged_spec_overrides if schema_override else {},
            "diagnoses": [d.model_dump() for d in merged_diagnoses],
        }


def _find_endpoint(
    endpoints: List[Any], method: str, path: str
) -> Endpoint | None:
    for ep in endpoints:
        if isinstance(ep, dict):
            if ep.get("method") == method and ep.get("path") == path:
                return Endpoint(**ep)
        else:
            if ep.method.value == method and ep.path == path:
                return ep
    return None


def heal_payload(state: TestState) -> Dict[str, Any]:
    s = _ensure_state(state) if isinstance(state, dict) else state
    current_key = s.current_endpoint
    if not current_key:
        return {"error": "No current endpoint to heal"}

    retries = s.retry_map.get(current_key, 0)
    retries += 1

    raw_payload = s.payloads.get(current_key, {})
    payload = PayloadTemplate(**raw_payload) if isinstance(raw_payload, dict) else raw_payload

    raw_log = s.results.get(current_key, {})
    violations_raw = raw_log.get("violations", []) if isinstance(raw_log, dict) else []

    vtype = ViolationType.UNKNOWN
    first_violation = None
    if violations_raw:
        first_violation = ContractViolation(**violations_raw[0]) if isinstance(violations_raw[0], dict) else violations_raw[0]
        vt = first_violation.violation_type
        if vt:
            try:
                vtype = ViolationType(vt)
            except ValueError:
                vtype = ViolationType.UNKNOWN

    healed_payload = heal_payload_via_classifier(vtype, payload, first_violation)

    merged_payloads = dict(s.payloads)
    merged_payloads[current_key] = healed_payload.model_dump()
    merged_retries = dict(s.retry_map)
    merged_retries[current_key] = retries

    reasoning_step = {
        "step": len(s.reasoning_log) + 1,
        "endpoint": current_key,
        "action": "healed",
        "observation": f"Violation type: {vtype.value}",
        "decision": "retry" if retries < MAX_RETRIES else "abort",
        "explanation": f"Applied heuristic for {vtype.value} (attempt {retries}/{MAX_RETRIES})",
    }
    merged_log = list(s.reasoning_log)
    merged_log.append(reasoning_step)

    return {
        "payloads": merged_payloads,
        "retry_map": merged_retries,
        "current_endpoint": current_key,
        "reasoning_log": merged_log,
    }


def should_retry(state: TestState) -> str:
    s = _ensure_state(state) if isinstance(state, dict) else state
    current_key = s.current_endpoint
    if not current_key:
        return "abort"

    retries = s.retry_map.get(current_key, 0)
    if retries > 0 and retries < MAX_RETRIES:
        return "retry"
    return "abort"


def advance_queue(state: TestState) -> Dict[str, Any]:
    s = _ensure_state(state) if isinstance(state, dict) else state
    current_key = s.current_endpoint
    if current_key:
        remaining = [k for k in s.endpoint_queue if k != current_key]
    else:
        remaining = list(s.endpoint_queue)

    new_current = remaining[0] if remaining else None

    return {
        "endpoint_queue": remaining,
        "current_endpoint": new_current,
    }


def should_continue(state: TestState) -> str:
    s = _ensure_state(state) if isinstance(state, dict) else state
    if s.current_endpoint is None and not s.endpoint_queue:
        return "end"
    return "next"


def build_graph() -> StateGraph:
    workflow = StateGraph(TestState)

    workflow.add_node("plan_sequence", plan_sequence)
    workflow.add_node("generate_payload", generate_payload_node)
    workflow.add_node("execute_request", execute_request)
    workflow.add_node("validate_contract", validate_contract)
    workflow.add_node("heal_payload", heal_payload)
    workflow.add_node("advance_queue", advance_queue)

    workflow.set_entry_point("plan_sequence")

    workflow.add_conditional_edges(
        "plan_sequence",
        lambda s: "generate_payload" if (isinstance(s, dict) and s.get("endpoint_queue")) or (hasattr(s, "endpoint_queue") and s.endpoint_queue) else END,
    )

    workflow.add_edge("generate_payload", "execute_request")
    workflow.add_edge("execute_request", "validate_contract")

    workflow.add_conditional_edges(
        "validate_contract",
        _route_validate,
        {
            "heal_payload": "heal_payload",
            "advance_queue": "advance_queue",
        },
    )

    workflow.add_conditional_edges(
        "heal_payload",
        should_retry,
        {
            "retry": "execute_request",
            "abort": "advance_queue",
        },
    )

    workflow.add_conditional_edges(
        "advance_queue",
        should_continue,
        {
            "next": "generate_payload",
            "end": END,
        },
    )

    return workflow.compile()


def _should_heal(violations: List[ContractViolation]) -> bool:
    return any(v.violation_type in (
        ViolationType.SCHEMA_ERROR.value,
        ViolationType.VALIDATION_ERROR.value,
    ) for v in violations)


def _build_explanation(violations: List[ContractViolation]) -> str:
    counts: Dict[str, int] = {}
    for v in violations:
        vt = v.violation_type or "unknown"
        counts[vt] = counts.get(vt, 0) + 1
    parts = [f"{n}x {t}" for t, n in sorted(counts.items())]
    return ", ".join(parts) if parts else "no classification"


def _route_validate(state: TestState) -> str:
    current_key = state.get("current_endpoint") if isinstance(state, dict) else state.current_endpoint
    if not current_key:
        return "advance_queue"

    results = state.get("results", {}) if isinstance(state, dict) else state.results
    raw_log = results.get(current_key, {})
    if isinstance(raw_log, dict):
        violations_raw = raw_log.get("violations", [])
    else:
        violations_raw = getattr(raw_log, "violations", [])

    if not violations_raw:
        return "advance_queue"

    violations = [
        ContractViolation(**v) if isinstance(v, dict) else v
        for v in violations_raw
    ]

    if not _should_heal(violations):
        return "advance_queue"

    retry_map = state.get("retry_map", {}) if isinstance(state, dict) else state.retry_map
    retries = retry_map.get(current_key, 0)
    if retries >= MAX_RETRIES:
        return "advance_queue"

    return "heal_payload"