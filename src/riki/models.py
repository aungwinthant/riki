from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class HttpMethod(str, Enum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"


class Endpoint(BaseModel):
    path: str
    method: HttpMethod
    operation_id: Optional[str] = None
    summary: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    has_path_params: bool = False
    has_body: bool = False
    depends_on: List[str] = Field(
        default_factory=list, description="operation_ids this endpoint depends on"
    )
    success_status: Optional[int] = None


class ContractViolation(BaseModel):
    endpoint: str
    method: str
    expected_status: int
    actual_status: int
    field_path: Optional[str] = Field(
        None, description="JSON path of the offending field"
    )
    expected_type: Optional[str] = None
    actual_type: Optional[str] = None
    expected_value: Optional[Any] = None
    actual_value: Optional[Any] = None
    message: str


class ExecutionLog(BaseModel):
    endpoint: str
    method: str
    status: str  # PASS | FAIL | SKIP | ERROR
    request_payload: Optional[Dict[str, Any]] = None
    response_status: Optional[int] = None
    response_body: Optional[Any] = None
    violations: List[ContractViolation] = Field(default_factory=list)
    retry_count: int = 0
    duration_ms: float = 0.0


class PayloadTemplate(BaseModel):
    body: Optional[Dict[str, Any]] = None
    query: Optional[Dict[str, str]] = None
    path_params: Optional[Dict[str, str]] = None


class TestState(BaseModel):
    spec_path: str
    base_url: str
    raw_spec: Dict[str, Any] = Field(default_factory=dict)
    endpoints: List[Endpoint] = Field(default_factory=list)
    endpoint_queue: List[str] = Field(
        default_factory=list, description="Ordered list of endpoint keys (method:path)"
    )
    current_endpoint: Optional[str] = None
    memory: Dict[str, Any] = Field(
        default_factory=dict,
        description="Runtime variables extracted from responses (e.g. extracted IDs)",
    )
    payloads: Dict[str, PayloadTemplate] = Field(default_factory=dict)
    results: Dict[str, ExecutionLog] = Field(default_factory=dict)
    violations: List[ContractViolation] = Field(default_factory=list)
    retry_map: Dict[str, int] = Field(
        default_factory=dict, description="Retry count per endpoint key"
    )
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    error: Optional[str] = None


class PlannerOutput(BaseModel):
    ordered_keys: List[str] = Field(
        description="Topologically sorted endpoint keys (method:path)"
    )
    dependency_graph: Dict[str, List[str]] = Field(
        description="Map of endpoint key to list of dependencies"
    )