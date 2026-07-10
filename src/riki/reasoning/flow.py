from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from riki.models import AuthScheme, Endpoint, HttpMethod


def find_auth_flow(
    endpoints: List[Endpoint],
    auth: List[AuthScheme],
) -> Optional[str]:
    """Find a login/auth endpoint that can be used for token extraction.

    Scans endpoints for a POST endpoint matching login/auth/token in path.
    Returns the endpoint key (method:path) if found and the user has
    provided basic credentials to authenticate with.
    """
    has_basic = any(s.type == "basic" and s.username and s.password for s in auth)
    if not has_basic:
        return None

    for ep in endpoints:
        if ep.method != HttpMethod.POST:
            continue
        path_lower = ep.path.lower()
        if re.search(r"(login|auth|token|signin)", path_lower):
            return f"{ep.method.value}:{ep.path}"

    return None


def execute_auth_flow(
    base_url: str,
    endpoint_key: str,
    basic_auth: AuthScheme,
) -> Optional[Dict[str, Any]]:
    """Execute a login request with basic auth and extract the token response."""
    import asyncio

    from riki.tools import execute_http_request
    from riki.models import PayloadTemplate

    method, path = endpoint_key.split(":", 1)
    payload = PayloadTemplate(body=None, query=None, path_params=None)

    try:
        status, headers, body = asyncio.run(
            execute_http_request(base_url, method, path, payload, {}, auth=[basic_auth])
        )
    except Exception:
        return None

    if status not in (200, 201):
        return None

    if isinstance(body, dict):
        return body
    return None


def extract_token_from_response(
    response_body: Dict[str, Any],
) -> Optional[str]:
    """Extract a token from a login response body.

    Checks common token field names in order of precedence.
    """
    for key in ("access_token", "token", "id_token", "jwt", "api_key", "apiKey"):
        val = response_body.get(key)
        if val and isinstance(val, str):
            return val
    return None


def build_bearer_scheme(token: str) -> AuthScheme:
    """Create a Bearer AuthScheme from an extracted token."""
    return AuthScheme(type="bearer", token=token)
