# Riki — API Contract Tester

An autonomous, agentic API contract-testing system that scans repositories for endpoints, plans execution sequences, generates payloads, and validates responses against OpenAPI specs using a LangGraph-powered multi-agent engine.

## Features

- **Repo Scanner** — Finds OpenAPI specs by name or content, or scans source code for routing patterns (FastAPI, Gin, Express, etc.)
- **Ephemeral Spec Generation** — Builds a minimal OpenAPI 3.0.3 spec when no spec file exists
- **Multi-Auth Support** — Basic, Bearer, and API Key authentication; multiple schemes sent simultaneously (e.g. Basic + Bearer)
- **OpenAPI Security Detection** — Auto-detects `securitySchemes` from the spec and merges with CLI-provided credentials
- **LangGraph State Machine** — Cyclic graph with 6 nodes, retry routing, and dependency-driven execution
- **Topological Sorting** — POST before GET before DELETE within the same resource path
- **Memory Propagation** — IDs extracted from POST responses automatically fill path params in subsequent GET/DELETE calls
- **Deterministic Validation** — `openapi-core` for all schema matching (LLM has no role in validation)
- **Intelligent Retry** — Failed contracts retry up to 2x with mutated payloads
- **Markdown & JSON Reports** — Structured reports with contract violation details (field-level JSON path)

## Quick Start

```bash
# Clone and install
git clone git@github.com:aungwinthant/riki.git
cd riki
pip install -e .

# Start the mock test server
python -m src.riki.mock_server

# In another terminal, scan and test
riki init                          # scan repo, creates .riki/config.json
riki init --auth-basic admin:secret123 --auth-bearer tok-xyz  # with credentials
riki test -u http://localhost:8765  # run contract tests
riki report                        # generate reports
```

## CLI Usage

```bash
riki init                          # scan repo, create .riki/
riki test -u <base_url>            # run contract tests
riki test -u <base_url> -s <spec>  # run tests with specific spec
riki report                        # generate report from last run
riki report --json                 # also output JSON report
```

Or use aliased flags:

```bash
riki init -p ./my-project -u http://api.example.com \
  --auth-basic admin:secret123 --auth-bearer eyJhbGci...
riki test -u http://localhost:8765 --max-concurrency 10 \
  --auth-apikey abc123 --auth-apikey-name X-API-Key
riki test --auth-basic admin:secret123  # override config credentials
riki report -o ./results/report.md --json
```

## Project Structure

```
src/
└── riki/
    ├── __init__.py
    ├── __main__.py         # `python -m riki` entrypoint
    ├── cli/
    │   ├── __init__.py
    │   └── main.py         # Click-based CLI (init / test / report)
    ├── scanner/
    │   ├── __init__.py
    │   └── scanner.py      # Repo scanner for OpenAPI files & code routes
    ├── agents/
    │   └── __init__.py
    ├── engine/
    │   └── __init__.py
    ├── reporter/
    │   └── __init__.py
    ├── models.py           # Pydantic models
    ├── tools.py            # Spec parser, payload gen, httpx executor, validator
    ├── graph.py            # LangGraph state machine
    ├── main.py             # Legacy CLI entrypoint
    ├── mock_server.py      # Dev/test mock API
    └── sample_spec.yaml    # Pet Store OpenAPI 3.0 spec
```

## Updating

```bash
# If installed from GitHub
pip install --upgrade git+https://github.com/aungwinthant/riki.git

# If installed from a local clone
git pull && pip install -e .
```

## Requirements

- Python 3.9+
- Dependencies: langgraph, openapi-core, pydantic (v2), httpx, pyyaml, click

## License

MIT
