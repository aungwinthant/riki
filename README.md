# Riki — API Contract Tester

An autonomous, agentic API contract-testing system. Ingests an OpenAPI 3.0+ spec, plans execution via topological sort (POST → GET → DELETE), generates payloads, executes HTTP requests, and validates responses against the spec using `openapi-core`.

## Features

- **Repo Scanner** — Finds OpenAPI specs by name or content, or scans source code for routing patterns (FastAPI, Gin, Express, Hono, Django, Flask, Fiber, Echo)
- **Ephemeral Spec Generation** — Builds a minimal OpenAPI 3.0.3 spec from code-discovered routes when no spec file exists
- **Multi-Auth Support** — Basic, Bearer, and API Key authentication; multiple schemes sent simultaneously (e.g. Basic + Bearer as comma-separated `Authorization` header)
- **Auth Flow Detection** — Proactively discovers login/auth endpoints, exchanges basic credentials for a bearer token, and propagates it to downstream requests
- **Supervisor Architecture** — LangGraph state machine with specialized worker nodes: Executor, Classifier, Healer, Diagnostician, Reporter
- **Deterministic Validation** — `openapi-core` for all schema matching. LLM has no role in validation
- **Violation Classification** — Every contract failure is bucketed: `auth_dependent`, `missing_resource`, `schema_error`, `validation_error`, or `unknown`
- **Intelligent Retry** — Only healable violations (schema/validation errors) trigger payload mutation and retry (up to 2x). Non-healable violations (auth, missing resource) advance immediately
- **Schema Override** — When a 200 response is an array but the spec says `type: object`, the spec is patched at runtime and the correction is flagged in the report (never silent)
- **Smart Memory Propagation** — IDs extracted from POST/GET responses automatically fill path params in downstream requests. Supports array responses, nested wrappers (`data`, `results`, `items`), and namespaced keys (`pets_id`, `users_id`)
- **Diagnostician (deterministic)** — UNKNOWN violations are analyzed and produce a concrete `next_action`: `flag_as_bug`, `suggest_spec_fix`, or `skip`
- **LLM Diagnostician (opt-in)** — When `--llm-model` is provided, an LLM analyzes UNKNOWN violations alongside the deterministic Diagnostician; its output passes through a circuit-breaker that falls back to the deterministic result on any failure
- **Markdown & JSON Reports** — Reasoning log, spec overrides, and diagnoses sections with full audit trail

## Quick Start

```bash
# Clone and install
git clone git@github.com:aungwinthant/riki.git
cd riki
pip install -e .

# Start the mock test server
python -m src.riki.mock_server

# In another terminal, scan and test
riki init                              # scan repo, creates .riki/config.json
riki init --auth-basic admin:secret123 --auth-bearer tok-xyz  # with credentials
riki test -u http://localhost:8765     # run contract tests
riki report                            # generate reports
```

## CLI Usage

```bash
riki init -p ./my-project -u http://api.example.com \
  --auth-basic admin:secret123 --auth-bearer eyJhbGci...
riki test -u http://localhost:8765 --max-concurrency 10
riki test --auth-basic admin:secret123  # override config credentials
riki test --llm-model gpt-4o-mini      # enable LLM Diagnostician
riki test --llm-base-url http://localhost:11434/v1  # local Ollama
riki report -o ./results/report.md --json
```

## Project Structure

```
src/riki/
├── cli/main.py               # Click-based CLI (init / test / report)
├── scanner/scanner.py        # Repo scanner: OpenAPI files + source code routes
├── reasoning/
│   ├── classifier.py         # Violation type classifier (5 buckets)
│   ├── healer.py             # Deterministic payload healer (truncate, range clamp)
│   ├── diagnostician.py      # Deterministic UNKNOWN handler (next_action)
│   ├── llm_diagnostician.py  # Optional LLM Diagnostician (circuit-breaker)
│   └── flow.py               # Auth flow: login discovery, token extraction
├── models.py                 # Pydantic models: TestState, ContractViolation, etc.
├── tools.py                  # Spec loader, payload gen, httpx executor, validator
├── graph.py                  # LangGraph state machine (6 nodes + conditional edges)
├── main.py                   # Markdown/JSON report generator
├── mock_server.py            # Dev/test mock API
└── sample_spec.yaml          # Pet Store OpenAPI 3.0 spec
```

## Architecture

```
CLI (click):  init → scanner → .riki/config
              test  → LangGraph engine → .riki/report
              report → markdown/json

Graph (LangGraph cyclic state machine):
  plan_sequence → generate_payload → execute_request → validate_contract
                                                            ↕ (max 2 retries)
                                                         heal_payload
                                                              ↓
                                                         advance_queue → (loop)

Supervisor pattern:
                    ┌───────────────┐
                    │   Supervisor   │  (planner — owns queue, delegates)
                    └───────┬───────┘
              ┌─────────────┼─────────────┬──────────────┐
              ▼             ▼             ▼              ▼
        ┌──────────┐  ┌───────────┐ ┌─────────────┐ ┌──────────┐
        │ Executor │  │Classifier │ │Diagnostician│ │ Reporter │
        │(det.)    │  │(det.)     │ │(determ. +   │ │(det.)    │
        └──────────┘  └───────────┘ │LLM, opt-in) │ └──────────┘
                                    └─────────────┘
Diagnostician runs when Classifier returns UNKNOWN. Deterministic module
handles all cases; optional LLM runs alongside with circuit-breaker fallback.
```

## Testing

```bash
# Golden baseline (7/7 PASS against mock server)
pytest tests/test_baseline.py -v

# Unit tests for reasoning modules
pytest tests/test_reasoning.py -v

# Update baseline after intentional changes
pytest tests/test_baseline.py --run-baseline-update
```

## Requirements

- Python 3.9+
- Dependencies: `langgraph`, `openapi-core`, `pydantic` (v2), `httpx`, `pyyaml`, `click`

## License

MIT
