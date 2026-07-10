from __future__ import annotations

import base64
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
    auth: List[AuthScheme] = Field(default_factory=list)
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


class AuthScheme(BaseModel):
    type: str  # "basic" | "bearer" | "apiKey" | "oauth2"
    username: Optional[str] = None
    password: Optional[str] = None
    token: Optional[str] = None
    key: Optional[str] = None
    key_in: str = "header"
    key_name: str = "X-API-Key"

    def to_headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {}
        if self.type == "basic" and self.username and self.password:
            raw = f"{self.username}:{self.password}"
            encoded = base64.b64encode(raw.encode()).decode()
            headers["Authorization"] = f"Basic {encoded}"
        elif self.type == "bearer" and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        elif self.type == "apiKey" and self.key:
            if self.key_in == "header":
                headers[self.key_name] = self.key
        return headers


class PlannerOutput(BaseModel):
    ordered_keys: List[str] = Field(
        description="Topologically sorted endpoint keys (method:path)"
    )
    dependency_graph: Dict[str, List[str]] = Field(
        description="Map of endpoint key to list of dependencies"
    )