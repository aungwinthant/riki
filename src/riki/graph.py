from __future__ import annotations

import random
import time
from typing import Any, Dict, List

from langgraph.graph import END, StateGraph

from .models import (
    AuthScheme,
    ContractViolation,
    Endpoint,
    ExecutionLog,
    PayloadTemplate,
    TestState,
)
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


def validate_contract(state: TestState) -> Dict[str, Any]:
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
    log.violations.extend(schema_violations)

    merged_results = dict(s.results)
    if log.violations:
        log.status = "FAIL"
        log_dict = log.model_dump()
        log_dict["violations"] = [v.model_dump() for v in log.violations]
        merged_results[current_key] = log_dict
        return {
            "results": merged_results,
            "violations": [v.model_dump() for v in schema_violations],
            "current_endpoint": current_key,
        }
    else:
        log.status = "PASS"
        updated_memory = extract_memory_variables(
            log.response_body, {}, s.memory
        )
        merged_results[current_key] = log.model_dump()
        return {
            "results": merged_results,
            "memory": updated_memory,
            "current_endpoint": current_key,
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

    healed_payload = PayloadTemplate(
        body=_heal_dict(payload.body) if payload.body else None,
        query=_heal_dict(payload.query) if payload.query else None,
        path_params=_heal_dict(payload.path_params) if payload.path_params else None,
    )

    merged_payloads = dict(s.payloads)
    merged_payloads[current_key] = healed_payload.model_dump()
    merged_retries = dict(s.retry_map)
    merged_retries[current_key] = retries

    return {
        "payloads": merged_payloads,
        "retry_map": merged_retries,
        "current_endpoint": current_key,
    }


def _heal_dict(d: Dict[str, Any]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(v, str) and len(v) > 10:
            result[k] = v[:5] + str(random.randint(100, 999))
        elif isinstance(v, int):
            result[k] = v + random.randint(1, 10)
        elif isinstance(v, dict):
            result[k] = _heal_dict(v)
        else:
            result[k] = v
    return result


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


def _route_validate(state: TestState) -> str:
    current_key = state.get("current_endpoint") if isinstance(state, dict) else state.current_endpoint
    if not current_key:
        return "advance_queue"

    results = state.get("results", {}) if isinstance(state, dict) else state.results
    raw_log = results.get(current_key, {})
    if isinstance(raw_log, dict):
        violations = raw_log.get("violations", [])
    else:
        violations = getattr(raw_log, "violations", [])

    if not violations:
        return "advance_queue"

    retry_map = state.get("retry_map", {}) if isinstance(state, dict) else state.retry_map
    retries = retry_map.get(current_key, 0)
    if retries >= MAX_RETRIES:
        return "advance_queue"

    return "heal_payload"