from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin

import httpx
import yaml
from openapi_core import Spec, validate_response as core_validate_response
from openapi_core.testing import MockRequest, MockResponse

from .models import (
    AuthScheme,
    ContractViolation,
    Endpoint,
    ExecutionLog,
    HttpMethod,
    PayloadTemplate,
    PlannerOutput,
)


def load_spec(path: str) -> Dict[str, Any]:
    with open(path) as f:
        if path.endswith((".yaml", ".yml")):
            return yaml.safe_load(f)
        return json.load(f)


def create_openapi_spec(raw: Dict[str, Any]):
    return Spec.from_dict(raw)


def extract_endpoints(raw: Dict[str, Any]) -> List[Endpoint]:
    endpoints: List[Endpoint] = []
    paths = raw.get("paths", {})
    for path, methods in paths.items():
        for method, details in methods.items():
            method_upper = method.upper()
            if method_upper not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
                continue
            has_path_params = bool(re.findall(r"\{(\w+)\}", path))
            has_body = "requestBody" in details
            depends_on: List[str] = []

            if has_path_params:
                path_param_names = set(re.findall(r"\{(\w+)\}", path))
                for (
                    other_path,
                    other_methods,
                ) in paths.items():
                    for other_method, other_details in other_methods.items():
                        other_op_id = other_details.get("operationId", "")
                        if not other_op_id:
                            continue
                        other_method_upper = other_method.upper()
                        if other_method_upper in ("POST", "PUT", "PATCH"):
                            other_responses = other_details.get("responses", {})
                            for status_code, response in other_responses.items():
                                if status_code in ("200", "201", "default"):
                                    response_schema = (
                                        response.get("content", {})
                                        .get("application/json", {})
                                        .get("schema", {})
                                    )
                                    if response_schema and response_schema.get(
                                        "type"
                                    ) in ("object",) and any(
                                        p in str(response_schema.get("properties", {}))
                                        for p in path_param_names
                                    ):
                                        depends_on.append(other_op_id)

            success_status = None
            for code in ("200", "201", "204", "default"):
                if code in details.get("responses", {}):
                    success_status = int(code) if code != "default" else 200
                    break

            endpoints.append(
                Endpoint(
                    path=path,
                    method=HttpMethod(method_upper),
                    operation_id=details.get("operationId"),
                    summary=details.get("summary", ""),
                    tags=details.get("tags", []),
                    has_path_params=has_path_params,
                    has_body=has_body,
                    depends_on=depends_on,
                    success_status=success_status or 200,
                )
            )
    return endpoints


def topological_sort(endpoints: List[Endpoint]) -> PlannerOutput:
    dep_graph: Dict[str, List[str]] = {}
    endpoint_map: Dict[str, Endpoint] = {}

    for ep in endpoints:
        key = _endpoint_key(ep)
        endpoint_map[key] = ep

    for ep in endpoints:
        key = _endpoint_key(ep)
        deps: List[str] = []
        for dep_op_id in ep.depends_on:
            for other_key, other_ep in endpoint_map.items():
                if other_ep.operation_id == dep_op_id:
                    deps.append(other_key)
                    break
        dep_graph[key] = deps

    in_degree: Dict[str, int] = {k: 0 for k in endpoint_map}
    for key, deps in dep_graph.items():
        for dep in deps:
            if dep in in_degree:
                in_degree[key] = in_degree.get(key, 0) + 1

    priority_map: Dict[str, int] = {
        "POST": 0,
        "PUT": 1,
        "PATCH": 2,
        "GET": 3,
        "DELETE": 4,
    }

    queue = sorted(
        [k for k, d in in_degree.items() if d == 0],
        key=lambda k: priority_map.get(k.split(":")[0], 99),
    )

    ordered: List[str] = []
    while queue:
        queue.sort(key=lambda k: priority_map.get(k.split(":")[0], 99))
        node = queue.pop(0)
        ordered.append(node)
        for other_key, other_deps in dep_graph.items():
            if node in other_deps:
                in_degree[other_key] -= 1
                if in_degree[other_key] == 0:
                    queue.append(other_key)

    remaining = [k for k in endpoint_map if k not in ordered]
    remaining.sort(key=lambda k: priority_map.get(k.split(":")[0], 99))
    ordered.extend(remaining)

    return PlannerOutput(ordered_keys=ordered, dependency_graph=dep_graph)


def _endpoint_key(ep: Endpoint) -> str:
    return f"{ep.method.value}:{ep.path}"


def parse_endpoint_key(key: str) -> Tuple[str, str]:
    method, path = key.split(":", 1)
    return method, path


def build_request_schema(
    raw_spec: Dict[str, Any], method: str, path: str
) -> Tuple[Optional[Dict], Optional[Dict], Optional[Dict]]:
    paths = raw_spec.get("paths", {})
    methods = paths.get(path, {})
    details = methods.get(method.lower(), {})

    body_schema: Optional[Dict] = None
    if "requestBody" in details:
        content = details["requestBody"].get("content", {})
        body_schema = content.get("application/json", {}).get("schema")

    query_schema: Optional[Dict] = None
    params = details.get("parameters", [])
    query_params = [p for p in params if p.get("in") == "query"]
    if query_params:
        query_schema = {
            "type": "object",
            "properties": {p["name"]: p.get("schema", {}) for p in query_params},
            "required": [
                p["name"] for p in query_params if p.get("required", False)
            ],
        }

    path_schema: Optional[Dict] = None
    path_params = [p for p in params if p.get("in") == "path"]
    if path_params:
        path_schema = {
            "type": "object",
            "properties": {p["name"]: p.get("schema", {}) for p in path_params},
            "required": [p["name"] for p in path_params],
        }

    return body_schema, query_schema, path_schema


def resolve_ref(ref: str, raw_spec: Dict[str, Any]) -> Dict:
    parts = ref.lstrip("#/").split("/")
    current = raw_spec
    for part in parts:
        current = current.get(part, {})
    return current


def _resolve_schema(schema: Dict, raw_spec: Dict[str, Any]) -> Dict:
    if "$ref" in schema:
        return resolve_ref(schema["$ref"], raw_spec)
    return schema


def generate_payload(
    body_schema: Optional[Dict],
    query_schema: Optional[Dict],
    path_schema: Optional[Dict],
    raw_spec: Dict[str, Any],
    memory: Dict[str, Any],
    endpoint_key: str,
) -> PayloadTemplate:
    body = _generate_from_schema(body_schema, raw_spec, memory) if body_schema else None
    query = (
        {
            k: str(
                _generate_value(v.get("schema", v), raw_spec, memory)
            )
            for k, v in (
                query_schema.get("properties", {}).items()
                if query_schema
                else {}
            )
        }
        if query_schema
        else None
    )
    path_params = (
        {
            k: str(
                _generate_value(v, raw_spec, memory)
            )
            for k, v in (
                path_schema.get("properties", {}).items()
                if path_schema
                else {}
            )
        }
        if path_schema
        else None
    )
    if path_params:
        path_params = _resolve_path_params_from_memory(path_params, memory, endpoint_key)

    return PayloadTemplate(body=body, query=query, path_params=path_params)


def _resolve_path_params_from_memory(
    path_params: Dict[str, Any], memory: Dict[str, Any], endpoint_key: str
) -> Dict[str, Any]:
    for key in list(path_params.keys()):
        memory_val = memory.get(key)
        if memory_val is None:
            memory_val = memory.get("id")
        if memory_val is not None:
            path_params[key] = str(memory_val)
    return path_params


def _generate_from_schema(
    schema: Dict, raw_spec: Dict[str, Any], memory: Dict[str, Any]
) -> Dict[str, Any]:
    schema = _resolve_schema(schema, raw_spec)
    if schema.get("type") == "object":
        result: Dict[str, Any] = {}
        properties = schema.get("properties", {})
        required = set(schema.get("required", list(properties.keys())))
        for prop_name, prop_schema in properties.items():
            if prop_name in required:
                result[prop_name] = _generate_value(prop_schema, raw_spec, memory)
            else:
                result[prop_name] = _generate_value(prop_schema, raw_spec, memory)
        return result
    return {}


def _generate_value(
    schema: Dict, raw_spec: Dict[str, Any], memory: Dict[str, Any]
) -> Any:
    schema = _resolve_schema(schema, raw_spec)

    if "example" in schema:
        return schema["example"]

    if "default" in schema:
        return schema["default"]

    if "enum" in schema:
        return schema["enum"][0]

    schema_type = schema.get("type", "string")

    if schema_type == "string":
        fmt = schema.get("format", "")
        if fmt == "date-time":
            return "2025-01-01T00:00:00Z"
        if fmt == "date":
            return "2025-01-01"
        if fmt == "email":
            return "test@example.com"
        if fmt == "uri":
            return "https://example.com/resource"
        if fmt == "uuid":
            return "550e8400-e29b-41d4-a716-446655440000"
        max_len = schema.get("maxLength", 20)
        return "test_" + "x" * min(max_len - 5, 10)
    elif schema_type == "integer":
        minimum = schema.get("minimum", 1)
        return minimum
    elif schema_type == "number":
        minimum = schema.get("minimum", 1.0)
        return float(minimum)
    elif schema_type == "boolean":
        return True
    elif schema_type == "array":
        items_schema = schema.get("items", {})
        return [_generate_value(items_schema, raw_spec, memory)]
    elif schema_type == "object":
        return _generate_from_schema(schema, raw_spec, memory)

    return "test_value"


def generate_string_from_schema(
    schema: Dict, raw_spec: Dict[str, Any]
) -> str:
    return _generate_value(schema, raw_spec, {})


def inject_auth_headers(
    headers: Dict[str, str], auth: List[AuthScheme]
) -> Dict[str, str]:
    for scheme in auth:
        headers.update(scheme.to_headers())
    return headers


async def execute_http_request(
    base_url: str,
    method: str,
    path: str,
    payload: PayloadTemplate,
    memory: Dict[str, Any],
    auth: Optional[List[AuthScheme]] = None,
    timeout: int = 30,
) -> Tuple[int, Dict[str, str], Any]:
    formatted_path = path
    if payload.path_params:
        for key, val in payload.path_params.items():
            formatted_path = formatted_path.replace(f"{{{key}}}", str(val))

    url = urljoin(base_url.rstrip("/") + "/", formatted_path.lstrip("/"))

    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if auth:
        headers = inject_auth_headers(headers, auth)

    body = payload.body
    if body:
        for key, val in _find_all_strings(body):
            if isinstance(val, str) and val.startswith("{{") and val.endswith("}}"):
                mem_key = val[2:-2]
                resolved = memory.get(mem_key)
                if resolved is not None:
                    _set_nested(body, key, resolved)

    params = payload.query

    async with httpx.AsyncClient(timeout=timeout, verify=False) as client:
        response = await client.request(
            method=method.upper(),
            url=url,
            headers=headers,
            json=body,
            params=params,
        )

    response_headers = dict(response.headers)
    try:
        response_body = response.json()
    except Exception:
        response_body = response.text

    return response.status_code, response_headers, response_body


def _find_all_strings(obj: Any, prefix: str = "") -> List[Tuple[str, Any]]:
    results: List[Tuple[str, Any]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            full_key = f"{prefix}.{k}" if prefix else k
            if isinstance(v, str):
                results.append((full_key, v))
            results.extend(_find_all_strings(v, full_key))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            full_key = f"{prefix}[{i}]"
            if isinstance(v, str):
                results.append((full_key, v))
            results.extend(_find_all_strings(v, full_key))
    return results


def _set_nested(obj: Any, key_path: str, value: Any) -> None:
    parts = key_path.split(".")
    current = obj
    for i, part in enumerate(parts):
        if "[" in part:
            arr_key, idx_str = part.split("[")
            idx = int(idx_str.rstrip("]"))
            if isinstance(current, dict) and arr_key:
                current = current[arr_key]
            if isinstance(current, list):
                if i == len(parts) - 1:
                    current[idx] = value
                    return
                current = current[idx]
            continue
        if i == len(parts) - 1:
            current[part] = value
            return
        current = current[part]


def _extract_json_path_from_error(err: Any) -> Optional[str]:
    err_str = str(err)
    match = re.search(
        r"'(.*?)' is a required property|Additional properties are not allowed.*'(.*?)'",
        err_str,
    )
    if match:
        return match.group(1) or match.group(2)
    match = re.search(r"\['?(.*?)'?\]", err_str)
    if match:
        return match.group(1)
    return None


def validate_response(
    raw_spec: Dict[str, Any],
    method: str,
    path: str,
    status_code: int,
    headers: Dict[str, str],
    body: Any,
) -> List[ContractViolation]:
    violations: List[ContractViolation] = []

    try:
        spec = create_openapi_spec(raw_spec)
        request = MockRequest(
            "http://localhost",
            method.upper(),
            path,
        )
        response = MockResponse(
            data=json.dumps(body).encode() if not isinstance(body, bytes) else body,
            status_code=status_code,
            headers={"Content-Type": headers.get("content-type", "application/json")},
        )
        result = core_validate_response(request, response, spec)

    except Exception as e:
        violations.append(
            ContractViolation(
                endpoint=path,
                method=method.upper(),
                expected_status=_extract_expected_status(e, status_code),
                actual_status=status_code,
                field_path=_extract_json_path_from_error(e),
                message=str(e),
            )
        )

    return violations


def _extract_expected_status(err: Any, actual: int) -> int:
    err_str = str(err)
    match = re.search(r"(\d{3})", err_str)
    return int(match.group(1)) if match else (200 if actual < 400 else actual)


def extract_memory_variables(
    body: Any, response_headers: Dict[str, str], memory: Dict[str, Any]
) -> Dict[str, Any]:
    updated = dict(memory)
    if isinstance(body, dict):

        id_fields = ["id", "ID", "Id", "resource_id", "userId", "user_id", "token", "slug"]
        for field in id_fields:
            val = body.get(field)
            if val is not None:
                updated[field] = val

        if "data" in body and isinstance(body["data"], dict):
            for field in id_fields:
                val = body["data"].get(field)
                if val is not None:
                    updated[field] = val

    for header_name in ("X-Request-Id", "Location", "ETag"):
        val = response_headers.get(header_name)
        if val:
            key = header_name.lower().replace("-", "_")
            updated[key] = val

    return updated


def detect_auth_schemes(raw_spec: Dict[str, Any]) -> List[AuthScheme]:
    schemes: List[AuthScheme] = []
    security_schemes = (
        raw_spec.get("components", {}).get("securitySchemes", {})
    )

    for name, definition in security_schemes.items():
        s_type = definition.get("type", "")
        if s_type == "http":
            http_scheme = definition.get("scheme", "").lower()
            if http_scheme == "basic":
                schemes.append(AuthScheme(type="basic"))
            elif http_scheme == "bearer":
                schemes.append(AuthScheme(type="bearer"))
        elif s_type == "apiKey":
            schemes.append(
                AuthScheme(
                    type="apiKey",
                    key_in=definition.get("in", "header"),
                    key_name=definition.get("name", "X-API-Key"),
                )
            )

    return schemes