# AGENTS.md — Riki (API Contract Tester)

## Project Overview
**Riki** is an autonomous API Contract-Testing System. Ingests an OpenAPI 3.0+ spec, plans execution via topological sort (POST → GET → DELETE), generates payloads, executes HTTP requests, and validates responses against the spec using `openapi-core`.

## Architecture

```
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
| `src/riki/models.py` | `TestState`, `ContractViolation`, `ExecutionLog`, `Endpoint`, `PayloadTemplate` |
| `src/riki/tools.py` | Spec loader, endpoint extractor, topological sort, payload gen (deterministic), httpx executor, openapi-core validator, memory variable extractor |
| `src/riki/graph.py` | 6 LangGraph nodes, conditional edges (retry/abort routing), compiled `StateGraph` |
| `src/riki/main.py` | CLI entrypoint, `ainvoke` runner, Markdown/JSON report generator |

## Running

```bash
riki <spec.yaml> <base_url> [-o report.md]
python -m src.riki.mock_server  # test server on :8765
```

## Key Behaviors
- **Topological order**: POST first, GET middle, DELETE last within same resource
- **Memory propagation**: IDs extracted from POST responses automatically fill path params in subsequent GET/DELETE calls
- **Retry**: Failed contracts retry up to 2x with `heal_payload` (mutates payload values)
- **Validation**: Uses `openapi-core` for deterministic schema matching; LLM has NO role in validation
- **State merging**: All node returns merge into shared `TestState` (LangGraph merge); nodes must copy-and-extend dict fields like `results`, `payloads`, `retry_map`

## Design Decisions
- `PayloadTemplate` uses `Dict[str, str]` for query/path params (values always cast to string)
- Mock server is single-threaded `HTTPServer` — only for dev testing
- `_ensure_state()` converts raw dict→TestState at each node entry to handle LangGraph's mixed dict/object state