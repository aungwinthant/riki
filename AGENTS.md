# AGENTS.md — Riki (API Contract Tester)

## Project Overview
**Riki** is an autonomous API Contract-Testing System. Ingests an OpenAPI 3.0+ spec, plans execution via topological sort (POST → GET → DELETE), generates payloads, executes HTTP requests, and validates responses against the spec using `openapi-core`. Supports three CLI commands: `init`, `test`, `report`.

## Architecture

```
CLI (click):  init → scanner → .riki/config
              test  → LangGraph engine (6-node state machine)
              report → markdown/json reporter

Graph (LangGraph cyclic state machine):
  plan_sequence → generate_payload → execute_request → validate_contract
                                                            ↕ (max 2 retries)
                                                         heal_payload
                                                              ↓
                                                         advance_queue → (loop)

Supervisor pattern (implemented):
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
(diagnostician.py) handles all cases; optional LLM (llm_diagnostician.py)
runs on the same violations and its output passes through a circuit-breaker
that falls back to the deterministic result on any failure.
```

## Key Files

| File | What |
|------|------|
| `src/riki/cli/main.py` | Click-based CLI entrypoint: `init`, `test`, `report` commands |
| `src/riki/scanner/scanner.py` | Repo scanner: finds OpenAPI specs by name/content; scans source code for routes (FastAPI, Gin, Express, etc.) with mount prefix detection |
| `src/riki/models.py` | `TestState`, `ContractViolation`, `ExecutionLog`, `Endpoint`, `PayloadTemplate`, `AuthScheme` |
| `src/riki/tools.py` | Spec loader, endpoint extractor, topological sort, payload gen, httpx executor, openapi-core validator, memory variable extractor |
| `src/riki/graph.py` | 6 LangGraph nodes, conditional edges (retry/abort routing), compiled `StateGraph` |
| `src/riki/reasoning/flow.py` | Auth flow detection: login endpoint discovery, token extraction, bearer scheme builder |
| `src/riki/reasoning/classifier.py` | Violation type classifier: `AUTH_DEPENDENT`, `MISSING_RESOURCE`, `SCHEMA_ERROR`, `VALIDATION_ERROR`, `UNKNOWN` |
| `src/riki/reasoning/healer.py` | Deterministic payload healer: truncation, range clamping per error message |
| `src/riki/reasoning/diagnostician.py` | Deterministic Diagnostician for UNKNOWN violations: `flag_as_bug`, `suggest_spec_fix`, `skip` |
| `src/riki/reasoning/llm_diagnostician.py` | Optional LLM Diagnostician with circuit-breaker fallback to deterministic |
| `src/riki/main.py` | Legacy CLI entrypoint (argparse), Markdown/JSON report generator |
| `src/riki/mock_server.py` | Test mock server with `/pets` and `/users` CRUD, configurable auth modes |

## Running

```bash
riki init                    # scan repo, create .riki/
riki test -u <base_url>      # run contract tests
riki report                  # generate report from last run
python -m src.riki.mock_server  # test server on :8765 (default no auth)
python -m src.riki.mock_server basic+bearer  # with dual auth
```

## Key Behaviors
- **Topological order**: POST first, GET middle, DELETE last within same resource
- **Memory propagation**: IDs extracted from POST responses automatically fill path params in subsequent GET/DELETE calls. Supports array responses (extracts from first element), nested wrappers (`data`, `results`, `items`), and namespaced keys (`pets_id`, `users_id`)
- **Schema override**: When a 200 response is an array but spec says `type: object`, `validate_contract` patches the spec and flags it as an auto-correction (never silent)
- **Auth flow**: `reasoning/flow.py` detects login/auth endpoints, executes them with basic creds, extracts token, and builds a bearer scheme for downstream requests
- **Classifier**: Pure function that maps `(response, violation) → ViolationType` — AUTH_DEPENDENT (401/403), MISSING_RESOURCE (404), SCHEMA_ERROR (200+type mismatch), VALIDATION_ERROR (422), UNKNOWN
- **Healer**: Pure function that adjusts payload values per violation type (truncate strings on maxLength, clamp integers on minimum/maximum)
- **Retry**: Failed contracts retry up to 2x with `heal_payload` (deterministic healing via `healer.py`)
- **Validation**: Uses `openapi-core` for deterministic schema matching; LLM has NO role in validation
- **State merging**: All node returns merge into shared `TestState` (LangGraph merge); nodes must copy-and-extend dict fields like `results`, `payloads`, `retry_map`
- **Scanner ignores**: frontend dirs (components, assets, public), test files, `.git`/`node_modules`/`.venv`
- **Ephemeral spec**: When no OpenAPI file found, scanner builds a minimal 3.0.3 spec from code-discovered routes
- **Scanner Express mounts**: Detects `app.use('/prefix', router)` patterns and resolves `require()`/`import` to trace router files to their mount prefixes
- **Auth**: `AuthScheme` supports basic, bearer, and apiKey types; multiple schemes stack (e.g. basic+bearer sent as single comma-separated `Authorization` header, dual-auth compatible)

## Design Decisions
- `PayloadTemplate` uses `Dict[str, str]` for query/path params (values always cast to string)
- Mock server is single-threaded `HTTPServer` — only for dev testing
- `_ensure_state()` converts raw dict→TestState at each node entry to handle LangGraph's mixed dict/object state
- Scanner detects specs by filename (`*openapi*`, `*swagger*`) AND by content (`openapi:` key in first 4KB)
- Route patterns cover FastAPI, Flask, Gin, Fiber, Echo, Express, Hono, and Django `path()`
- **Reasoning modules are deterministic**: Classifier and Healer are pure functions with no side effects, fully unit-testable in isolation
- **LLM is NOT required**: The Diagnostician (LLM) is only invoked when Classifier returns `UNKNOWN`, and its output passes through a deterministic Supervisor circuit-breaker
- **Circuit-breaker**: When LLM Diagnostician fails (network error, invalid JSON, malformed action), its result is silently discarded and the deterministic Diagnostician's output is used instead
- **Schema overrides are always reported**: Never applied silently — they appear in both the JSON report and reasoning log
- **Diagnostician**: When Classifier returns UNKNOWN, deterministic Diagnostician produces a concrete next_action (flag_as_bug / suggest_spec_fix / skip). Optional LLM Diagnostician runs on the same violations with circuit-breaker fallback
- **Report**: Markdown report includes Reasoning Log (all classify/heal/diagnose decisions), Spec Overrides (flagged auto-corrections), and Diagnoses (UNKNOWN analysis with actions)
