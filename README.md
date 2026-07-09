# Riki — API Contract Tester

An autonomous, agentic API contract-testing system that ingests OpenAPI 3.0+ specs, plans execution sequences based on resource dependencies, generates payloads, executes HTTP requests, and deterministically validates responses against the spec.

## Features

- **LangGraph State Machine** — Cyclic graph with 6 nodes, retry routing, and dependency-driven execution
- **Topological Sorting** — POST before GET before DELETE within the same resource path
- **Memory Propagation** — IDs extracted from POST responses automatically fill path params in subsequent GET/DELETE calls
- **Deterministic Validation** — `openapi-core` for all schema matching (LLM has no role in validation)
- **Intelligent Retry** — Failed contracts retry up to 2x with mutated payloads
- **Markdown Reports** — Structured reports with contract violation details (field-level JSON path)

## Quick Start

```bash
# Clone and install
git clone git@github.com:aungwinthant/riki.git
cd riki
pip install -e .

# Start the mock test server
python -m src.riki.mock_server

# In another terminal, run the tester
riki src/riki/sample_spec.yaml http://localhost:8765 -o report.md
```

## CLI Usage

```bash
riki <spec> <base_url> [-o OUTPUT] [--json]

Arguments:
  spec          Path to OpenAPI 3.0+ spec (JSON or YAML)
  base_url      Target API base URL
  -o OUTPUT     Output Markdown report path (default: test_report.md)
  --json        Also output JSON report alongside Markdown
```

Or via Python module:

```bash
python -m src.riki.main src/riki/sample_spec.yaml http://localhost:8765
```

## Project Structure

```
src/
└── riki/
    ├── __init__.py
    ├── __main__.py       # `python -m riki` entrypoint
    ├── models.py         # Pydantic models
    ├── tools.py          # Spec parser, payload gen, httpx executor, openapi-core validator
    ├── graph.py          # LangGraph state machine
    ├── main.py           # CLI entrypoint + report generator
    ├── mock_server.py    # Dev/test mock API
    └── sample_spec.yaml  # Pet Store OpenAPI 3.0 spec
```

## Requirements

- Python 3.9+
- Dependencies: langgraph, openapi-core, pydantic (v2), httpx, pyyaml

## License

MIT