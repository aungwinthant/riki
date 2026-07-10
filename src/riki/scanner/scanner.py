from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Set

import yaml

IGNORE_DIRS: Set[str] = {
    ".git",
    "__pycache__",
    "node_modules",
    "vendor",
    ".venv",
    "venv",
    ".env",
    "dist",
    "build",
    ".riki",
    ".tox",
    ".eggs",
    "eggs",
    "target",
    "bower_components",
    ".next",
    ".nuxt",
}

IGNORE_FILE_PATTERNS: List[re.Pattern] = [
    re.compile(r".*\.test\.(py|js|ts|tsx|jsx|go)$"),
    re.compile(r".*\.spec\.(py|js|ts|tsx|jsx|go)$"),
    re.compile(r".*_test\.(py|go)$"),
]

FRONTEND_DIR_HINTS: Set[str] = {
    "components",
    "pages",
    "assets",
    "public",
    "static",
    "styles",
    "css",
    "fonts",
    "images",
    "frontend",
    "client",
    "ui",
    "theme",
    "layouts",
    "views",
}

SOURCE_EXTENSIONS: Dict[str, Set[str]] = {
    "python": {".py"},
    "go": {".go"},
    "javascript": {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"},
}

ROUTE_PATTERNS: Dict[str, List[Dict[str, Any]]] = {
    "python": [
        {
            # FastAPI: @router.get("/path"), @app.post("/path", ...)
            "pattern": re.compile(
                r'@\w+\.(get|post|put|patch|delete|options|head)\s*\([^)]*["\'](/[^"\']*)["\']'
            ),
            "method_group": 1,
            "path_group": 2,
        },
        {
            # Flask: @app.route("/path", methods=["GET"])
            "pattern": re.compile(
                r'@\w+\.route\s*\(\s*["\'](/[^"\']*)["\'][^)]*methods\s*=\s*\['
                r"(?:\s*['\"](\w+)['\"]\s*(?:,\s*['\"](\w+)['\"])*)\]"
            ),
            "method_group": 2,
            "path_group": 1,
        },
        {
            # Flask-RESTful: api.add_resource(Handler, "/path")
            "pattern": re.compile(
                r"""\.add_resource\s*\(\s*\w+\s*,\s*["\'](/[^"\']*)["\']"""
            ),
            "method_group": None,
            "path_group": 1,
        },
        {
            # Django URL patterns: path("/path", views.handler)
            "pattern": re.compile(
                r"""path\s*\(\s*["\'](/[^"\']*)["\']"""
            ),
            "method_group": None,
            "path_group": 1,
        },
    ],
    "go": [
        {
            # Gin: r.GET("/path", handler), router.POST("/path", ...)
            "pattern": re.compile(
                r"""\.(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s*\(\s*["\`](/[^"\`]*)["\`]"""
            ),
            "method_group": 1,
            "path_group": 2,
        },
        {
            # Fiber: app.Get("/path", handler)
            "pattern": re.compile(
                r"""\.(Get|Post|Put|Patch|Delete|Head|Options)\s*\(\s*["\`](/[^"\`]*)["\`]"""
            ),
            "method_group": 1,
            "path_group": 2,
        },
        {
            # Gorilla/Mux: r.HandleFunc("/path", handler).Methods("GET")
            "pattern": re.compile(
                r"""\.Handle(?:Func)?\s*\(\s*["\`](/[^"\`]*)["\`]"""
            ),
            "method_group": None,
            "path_group": 1,
        },
        {
            # Echo: e.GET("/path", handler)
            "pattern": re.compile(
                r"""\.(GET|POST|PUT|PATCH|DELETE)\s*\(\s*["\`](/[^"\`]*)["\`]"""
            ),
            "method_group": 1,
            "path_group": 2,
        },
    ],
    "javascript": [
        {
            # Express/Fastify: router.get("/path", handler)
            "pattern": re.compile(
                r"""\.(get|post|put|patch|delete|head|options)\s*\(\s*["\`](/[^"\`]*)["\`]"""
            ),
            "method_group": 1,
            "path_group": 2,
        },
        {
            # Next.js API routes: export default handler (method inferred)
            "pattern": re.compile(
                r"""export\s+(default\s+)?(?:async\s+)?function\s+\w+"""
            ),
            "method_group": None,
            "path_group": None,
        },
        {
            # Hono: app.get("/path", handler)
            "pattern": re.compile(
                r"""\.(get|post|put|patch|delete)\s*\(\s*["\`](/[^"\`]*)["\`]"""
            ),
            "method_group": 1,
            "path_group": 2,
        },
    ],
}


def _should_ignore(path: Path, root: Path) -> bool:
    rel = path.relative_to(root).as_posix()
    parts = rel.split("/")
    for part in parts:
        if part in IGNORE_DIRS:
            return True
    for pattern in IGNORE_FILE_PATTERNS:
        if pattern.match(path.name):
            return True
    return False


def _has_frontend_hint(path: Path) -> bool:
    parts = path.as_posix().split("/")
    return any(p in FRONTEND_DIR_HINTS for p in parts)


def _find_spec_files(root: Path) -> List[Path]:
    results: List[Path] = []
    spec_extensions = {".yaml", ".yml", ".json"}

    for path in root.rglob("*"):
        if _should_ignore(path, root):
            continue
        if not path.is_file() or path.suffix not in spec_extensions:
            continue
        name_lower = path.stem.lower()
        if "openapi" in name_lower or "swagger" in name_lower:
            results.append(path)
            continue

        if path.stat().st_size > 1_048_576:
            continue

        try:
            with open(path, "rb") as f:
                head = f.read(4096)
            if b"openapi" in head.lower() or b'"openapi"' in head.lower():
                results.append(path)
        except Exception:
            continue

    return results


def _scan_source_files(root: Path) -> List[Tuple[Path, str]]:
    files: List[Tuple[Path, str]] = []
    for path in root.rglob("*"):
        if _should_ignore(path, root):
            continue
        if not path.is_file():
            continue
        if _has_frontend_hint(path):
            continue

        for lang, exts in SOURCE_EXTENSIONS.items():
            if path.suffix in exts:
                files.append((path, lang))
                break
    return files


def _extract_routes_from_file(
    path: Path, lang: str
) -> List[Dict[str, str]]:
    routes: List[Dict[str, str]] = []
    patterns = ROUTE_PATTERNS.get(lang, [])
    if not patterns:
        return routes

    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return routes

    for entry in patterns:
        for match in entry["pattern"].finditer(content):
            method_group = entry["method_group"]
            path_group = entry["path_group"]

            if path_group is None:
                continue

            raw_path = match.group(path_group)
            clean_path = raw_path.rstrip("/") if raw_path != "/" else "/"

            if method_group is not None:
                method = match.group(method_group).upper()
                routes.append(
                    {"method": method, "path": clean_path, "source": str(path)}
                )
            else:
                for inferred in ("GET", "POST", "PUT", "DELETE"):
                    routes.append(
                        {
                            "method": inferred,
                            "path": clean_path,
                            "source": str(path),
                        }
                    )

    return routes


def _normalise_path_template(raw: str) -> str:
    cleaned = re.sub(r"<(\w+)>", r"{\1}", raw)
    cleaned = re.sub(r":(\w+)", r"{\1}", cleaned)
    return cleaned


def _deduplicate_routes(
    routes: List[Dict[str, str]],
) -> List[Dict[str, str]]:
    seen: Set[Tuple[str, str]] = set()
    unique: List[Dict[str, str]] = []
    for r in routes:
        norm_path = _normalise_path_template(r["path"])
        key = (r["method"], norm_path)
        if key not in seen:
            seen.add(key)
            unique.append({**r, "path": norm_path})
    return unique


def _build_ephemeral_spec(
    routes: List[Dict[str, str]], output_dir: Path
) -> Path:
    spec: Dict[str, Any] = {
        "openapi": "3.0.3",
        "info": {
            "title": "Ephemeral Spec (Code-Generated)",
            "version": "0.1.0",
            "description": (
                "Auto-generated OpenAPI specification produced by riki scanner "
                "from source-code route patterns. This is a minimal spec; "
                "response schemas are placeholders."
            ),
        },
        "paths": {},
    }

    for r in routes:
        method = r["method"].lower()
        path = r["path"]
        if path not in spec["paths"]:
            spec["paths"][path] = {}
        if method not in spec["paths"][path]:
            spec["paths"][path][method] = {
                "summary": f"{r['method']} {path}",
                "operationId": f"{method}_{path.replace('/', '_').replace('{', '').replace('}', '').strip('_')}",
                "responses": {
                    "200": {
                        "description": "Successful response",
                        "content": {
                            "application/json": {"schema": {"type": "object"}}
                        },
                    }
                },
            }

            path_params = re.findall(r"\{(\w+)\}", path)
            if path_params:
                spec["paths"][path][method]["parameters"] = [
                    {
                        "name": p,
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                    for p in path_params
                ]

    output_dir.mkdir(parents=True, exist_ok=True)
    spec_path = output_dir / "ephemeral_spec.yaml"
    with open(spec_path, "w") as f:
        yaml.dump(spec, f, default_flow_style=False, sort_keys=False)

    return spec_path


def scan_repository(root: Path) -> Dict[str, Any]:
    """Scan a repository root for API endpoints.

    Returns a dictionary with keys:
      - spec_path:    Path to discovered OpenAPI spec, or None
      - discovered_routes: List of {method, path, source} from code scan
      - ephemeral_spec_path: Path to generated ephemeral spec, or None
    """
    root = root.resolve()
    result: Dict[str, Any] = {
        "spec_path": None,
        "discovered_routes": [],
        "ephemeral_spec_path": None,
    }

    spec_files = _find_spec_files(root)
    if spec_files:
        result["spec_path"] = str(spec_files[0])
        return result

    source_files = _scan_source_files(root)
    all_routes: List[Dict[str, str]] = []
    for file_path, lang in source_files:
        routes = _extract_routes_from_file(file_path, lang)
        all_routes.extend(routes)

    unique_routes = _deduplicate_routes(all_routes)
    result["discovered_routes"] = unique_routes

    if unique_routes:
        output_dir = root / ".riki"
        spec_path = _build_ephemeral_spec(unique_routes, output_dir)
        result["ephemeral_spec_path"] = str(spec_path)

    return result
