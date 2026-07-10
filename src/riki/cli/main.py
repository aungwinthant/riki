from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Optional

import click

from riki.scanner.scanner import scan_repository

CONFIG_DIR = ".riki"
CONFIG_FILE = "config.json"
REPORT_FILE = "test_report.md"


def _load_config() -> dict:
    cfg_path = Path.cwd() / CONFIG_DIR / CONFIG_FILE
    if not cfg_path.exists():
        return {}
    with open(str(cfg_path)) as f:
        return json.load(f)


def _save_config(cfg: dict) -> None:
    cfg_dir = Path.cwd() / CONFIG_DIR
    cfg_dir.mkdir(parents=True, exist_ok=True)
    with open(str(cfg_dir / CONFIG_FILE), "w") as f:
        json.dump(cfg, f, indent=2)


@click.group()
@click.version_option(version="0.2.0", message="riki %(version)s")
def cli():
    """Riki — Autonomous API Contract Testing System.

    Scans repositories, discovers API endpoints, and orchestrates
    multi-agent contract validation against live services.
    """


# Shared auth options reused across commands
AUTH_OPTIONS = [
    click.option(
        "--auth-basic",
        "auth_basic",
        default=None,
        help="Basic auth credentials as `username:password`",
    ),
    click.option(
        "--auth-bearer",
        "auth_bearer",
        default=None,
        help="Bearer token for Authorization header",
    ),
    click.option(
        "--auth-apikey",
        "auth_apikey",
        default=None,
        help="API key value",
    ),
    click.option(
        "--auth-apikey-name",
        "auth_apikey_name",
        default="X-API-Key",
        help="Header name for API key (default: X-API-Key)",
        show_default=True,
    ),
    click.option(
        "--auth-apikey-in",
        "auth_apikey_in",
        type=click.Choice(["header", "query"]),
        default="header",
        help="Location of the API key",
        show_default=True,
    ),
]


def _auth_from_params(
    auth_basic: Optional[str],
    auth_bearer: Optional[str],
    auth_apikey: Optional[str],
    auth_apikey_name: str,
    auth_apikey_in: str,
) -> list:
    schemes: list = []
    if auth_basic:
        parts = auth_basic.split(":", 1)
        schemes.append({
            "type": "basic",
            "username": parts[0] if len(parts) > 0 else "",
            "password": parts[1] if len(parts) > 1 else "",
        })
    if auth_bearer:
        schemes.append({"type": "bearer", "token": auth_bearer})
    if auth_apikey:
        schemes.append({
            "type": "apiKey",
            "key": auth_apikey,
            "key_name": auth_apikey_name,
            "key_in": auth_apikey_in,
        })
    return schemes


@cli.command()
@click.option(
    "--path",
    "-p",
    default=".",
    help="Repository root path to scan (default: current directory)",
    show_default=True,
)
@click.option(
    "--base-url",
    "-u",
    default="http://localhost:8000",
    help="Base URL of the target API (saved to config)",
    show_default=True,
)
@click.option(
    "--auth-basic",
    default=None,
    help="Basic auth credentials as `username:password`",
)
@click.option(
    "--auth-bearer",
    default=None,
    help="Bearer token for Authorization header",
)
@click.option(
    "--auth-apikey",
    default=None,
    help="API key value",
)
@click.option(
    "--auth-apikey-name",
    default="X-API-Key",
    help="Header name for API key (default: X-API-Key)",
    show_default=True,
)
@click.option(
    "--auth-apikey-in",
    type=click.Choice(["header", "query"]),
    default="header",
    help="Location of the API key",
    show_default=True,
)
def init(path: str, base_url: str, **auth_kwargs):
    """Scan the repository and initialise .riki/ configuration.

    Discovers OpenAPI specification files (*openapi*, *swagger*, *.yaml,
    *.yml, *.json) AND/OR scans source code for routing patterns
    (FastAPI, Gin, Express, etc.), then merges both sources to build
    a comprehensive ephemeral spec.
    """
    root = Path(path).resolve()
    if not root.is_dir():
        click.echo(f"Error: {root} is not a valid directory", err=True)
        raise SystemExit(1)

    click.echo(f"Scanning {root} ...")

    result = scan_repository(root)

    spec_path = result.get("spec_path")
    routes = result.get("discovered_routes", [])

    if spec_path:
        click.echo(f"  Found OpenAPI spec: {spec_path}")
    if routes:
        if spec_path:
            click.echo(f"  Discovered {len(routes)} route(s) from spec and source code")
        else:
            click.echo(f"  Discovered {len(routes)} route(s) via code scan")
        for r in routes:
            click.echo(f"    {r['method']:6s} {r['path']}")
    else:
        click.echo("  No API routes discovered.")

    if not spec_path and routes:
        ephemeral = result.get("ephemeral_spec_path")
        if ephemeral:
            spec_path = ephemeral

    auth_schemes = _auth_from_params(
        auth_kwargs.get("auth_basic"),
        auth_kwargs.get("auth_bearer"),
        auth_kwargs.get("auth_apikey"),
        auth_kwargs.get("auth_apikey_name", "X-API-Key"),
        auth_kwargs.get("auth_apikey_in", "header"),
    )

    cfg = {
        "base_url": base_url,
        "spec_path": spec_path or "ephemeral",
    }
    if auth_schemes:
        cfg["auth"] = auth_schemes
    _save_config(cfg)
    click.echo(f"\nConfiguration written to {Path(CONFIG_DIR) / CONFIG_FILE}")
    click.echo(f"  base_url : {base_url}")
    click.echo(f"  spec_path: {spec_path or 'ephemeral (code-generated)'}")
    for s in auth_schemes:
        click.echo(f"  auth     : {s['type']}")

    if result["spec_path"] or result.get("discovered_routes"):
        click.echo(
            f"\nRun `riki test` to begin contract testing against {base_url}"
        )
    else:
        click.echo(
            "\nNo API endpoints were detected. "
            "Provide an OpenAPI spec manually, or check the scan path."
        )


@cli.command()
@click.option(
    "--base-url",
    "-u",
    help="Base URL of the API (overrides config value)",
)
@click.option(
    "--spec",
    "-s",
    help="Path to OpenAPI spec file (overrides config value)",
)
@click.option(
    "--max-concurrency",
    default=5,
    help="Maximum number of parallel test workers",
    show_default=True,
)
@click.option(
    "--auth-basic",
    default=None,
    help="Basic auth credentials as `username:password`",
)
@click.option(
    "--auth-bearer",
    default=None,
    help="Bearer token for Authorization header",
)
@click.option(
    "--auth-apikey",
    default=None,
    help="API key value",
)
@click.option(
    "--auth-apikey-name",
    default="X-API-Key",
    help="Header name for API key",
    show_default=True,
)
@click.option(
    "--auth-apikey-in",
    type=click.Choice(["header", "query"]),
    default="header",
    help="Location of the API key",
    show_default=True,
)
def test(base_url: Optional[str], spec: Optional[str], max_concurrency: int, **auth_kwargs):
    """Execute contract tests against all discovered API endpoints.

    Launches a multi-agent swarm that plans execution order, generates
    payloads, sends requests, and validates responses against the spec.
    """
    cfg = _load_config()
    if not cfg:
        click.echo(
            "No configuration found. Run `riki init` first.", err=True
        )
        raise SystemExit(1)

    resolved_url = base_url or cfg.get("base_url")
    if not resolved_url:
        click.echo(
            "No base URL configured. Provide --base-url or run `riki init`.",
            err=True,
        )
        raise SystemExit(1)

    spec_path = spec or cfg.get("spec_path")
    if not spec_path or spec_path == "ephemeral":
        click.echo(
            "No spec file configured. Provide --spec or run `riki init` in a "
            "repository with API definitions.",
            err=True,
        )
        raise SystemExit(1)

    click.echo(f"Testing {resolved_url} with spec {spec_path} ...")
    click.echo(f"Max concurrency: {max_concurrency}")

    cli_auth = _auth_from_params(
        auth_kwargs.get("auth_basic"),
        auth_kwargs.get("auth_bearer"),
        auth_kwargs.get("auth_apikey"),
        auth_kwargs.get("auth_apikey_name", "X-API-Key"),
        auth_kwargs.get("auth_apikey_in", "header"),
    )
    cfg_auth = cfg.get("auth", [])
    merged_auth = cfg_auth + [s for s in cli_auth if s not in cfg_auth]
    for s in merged_auth:
        click.echo(f"  auth     : {s['type']}")

    start = time.time()
    from riki.graph import build_graph
    from riki.models import TestState

    graph = build_graph()
    initial = TestState(
        spec_path=spec_path,
        base_url=resolved_url,
        auth=merged_auth,
    )

    try:
        result = asyncio.run(
            graph.ainvoke(initial.model_dump(), {"recursion_limit": 200})
        )
    except Exception as exc:
        click.echo(f"\nError during test execution: {exc}", err=True)
        raise SystemExit(1)
    elapsed = time.time() - start

    state = TestState(**result)
    state.end_time = time.time()

    passed = sum(1 for l in state.results.values() if l.status == "PASS")
    failed = sum(1 for l in state.results.values() if l.status == "FAIL")
    errors = sum(1 for l in state.results.values() if l.status == "ERROR")

    click.echo(f"\nResults ({elapsed:.2f}s):")
    click.echo(f"  PASS  {passed}")
    click.echo(f"  FAIL  {failed}")
    click.echo(f"  ERROR {errors}")

    report_dir = Path.cwd() / CONFIG_DIR
    report_dir.mkdir(parents=True, exist_ok=True)
    md_path = str(report_dir / REPORT_FILE)
    json_path = str(report_dir / "test_report.json")

    md_content = _generate_markdown_report(state)
    with open(md_path, "w") as f:
        f.write(md_content)

    json_content = _generate_json_report(state)
    with open(json_path, "w") as f:
        f.write(json.dumps(json_content, indent=2, default=str))

    click.echo(f"\nReports written to {CONFIG_DIR}/")
    click.echo(f"  {REPORT_FILE}")
    click.echo(f"  test_report.json")


@cli.command()
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    default=None,
    help="Output path for the Markdown report (default: .riki/test_report.md)",
)
@click.option(
    "--json/--no-json",
    "emit_json",
    default=False,
    help="Also write a JSON report alongside the Markdown report",
)
def report(output: Optional[str], emit_json: bool):
    """Generate a test report from the most recent execution logs."""
    report_dir = Path.cwd() / CONFIG_DIR
    json_path = report_dir / "test_report.json"

    if not json_path.exists():
        click.echo(
            "No execution logs found. Run `riki test` first.", err=True
        )
        raise SystemExit(1)

    from riki.models import TestState

    with open(str(json_path)) as f:
        data = json.load(f)

    state = TestState(**data)
    md_content = _generate_markdown_report(state)

    out_path = str(Path(output)) if output else str(report_dir / REPORT_FILE)
    with open(out_path, "w") as f:
        f.write(md_content)
    click.echo(f"Markdown report written to {out_path}")

    if emit_json:
        json_out = str(Path(out_path).with_suffix(".json"))
        with open(json_out, "w") as f:
            f.write(json.dumps(data, indent=2, default=str))
        click.echo(f"JSON report written to {json_out}")


def _generate_markdown_report(state) -> str:
    from riki.main import generate_markdown_report as _old_gen
    return _old_gen(state)


def _generate_json_report(state) -> dict:
    return state.model_dump()
