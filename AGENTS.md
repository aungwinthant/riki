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
```

## Key Files

| File | What |
|------|------|
| `src/riki/cli/main.py` | Click-based CLI entrypoint: `init`, `test`, `report` commands |
| `src/riki/scanner/scanner.py` | Repo scanner: finds OpenAPI specs by name/content; scans source code for routes (FastAPI, Gin, Express, etc.) |
| `src/riki/models.py` | `TestState`, `ContractViolation`, `ExecutionLog`, `Endpoint`, `PayloadTemplate` |
| `src/riki/tools.py` | Spec loader, endpoint extractor, topological sort, payload gen (deterministic), httpx executor, openapi-core validator, memory variable extractor |
| `src/riki/graph.py` | 6 LangGraph nodes, conditional edges (retry/abort routing), compiled `StateGraph` |
| `src/riki/main.py` | Legacy CLI entrypoint (argparse), Markdown/JSON report generator |

## Running

```bash
riki init                    # scan repo, create .riki/
riki test -u <base_url>      # run contract tests
riki report                  # generate report from last run
python -m src.riki.mock_server  # test server on :8765
```

## Key Behaviors
- **Topological order**: POST first, GET middle, DELETE last within same resource
- **Memory propagation**: IDs extracted from POST responses automatically fill path params in subsequent GET/DELETE calls
- **Retry**: Failed contracts retry up to 2x with `heal_payload` (mutates payload values)
- **Validation**: Uses `openapi-core` for deterministic schema matching; LLM has NO role in validation
- **State merging**: All node returns merge into shared `TestState` (LangGraph merge); nodes must copy-and-extend dict fields like `results`, `payloads`, `retry_map`
- **Scanner ignores**: frontend dirs (components, assets, public), test files, `.git`/`node_modules`/`.venv`
- **Ephemeral spec**: When no OpenAPI file found, scanner builds a minimal 3.0.3 spec from code-discovered routes
- **Auth**: `AuthScheme` supports basic, bearer, and apiKey types; multiple schemes stack (e.g. basic+bearer sent together)
- **`plan_sequence`**: Merges spec-detected `securitySchemes` with user-provided credentials from `TestState.auth`
- **`execute_request`**: Passes all auth schemes to `execute_http_request` which calls `inject_auth_headers()`; each scheme renders its own header

## Design Decisions
- `PayloadTemplate` uses `Dict[str, str]` for query/path params (values always cast to string)
- Mock server is single-threaded `HTTPServer` — only for dev testing
- `_ensure_state()` converts raw dict→TestState at each node entry to handle LangGraph's mixed dict/object state
- Scanner detects specs by filename (`*openapi*`, `*swagger*`) AND by content (`openapi:` key in first 4KB)
- Route patterns cover FastAPI, Flask, Gin, Fiber, Echo, Express, Hono, and Django `path()`