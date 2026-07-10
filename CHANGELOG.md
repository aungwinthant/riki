# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Changelog entries are tied to Git tags. Each tagged release publishes the accumulated commit messages.

---

## [Unreleased]

### Fixed
- Express route regex now matches single-quoted path strings (e.g. `router.get('/path', handler)`)

### Added
- Initial project scaffold: `models.py`, `tools.py`, `graph.py`, `main.py`
- LangGraph cyclic state machine with `plan_sequence`, `generate_payload`, `execute_request`, `validate_contract`, `heal_payload`, and `advance_queue` nodes
- Topological sort planner (POST → GET → DELETE ordering)
- Deterministic payload generation from OpenAPI schemas with memory variable resolution
- HTTP request execution via async `httpx` client
- Response validation via `openapi-core` with field-level JSON path violation reporting
- Markdown test report generator with pass/fail summary and violation tables
- Mock test server implementing a Pet Store API
- Sample OpenAPI 3.0 spec (`sample_spec.yaml`) for end-to-end testing
- Retry logic (max 2 attempts) with `heal_payload` node for payload mutation
- Memory state propagation: IDs from POST responses automatically populate path params in GET/DELETE requests
- `AGENTS.md` with architecture overview for agent-assisted development

### Changed
- Restructured source from `api_contract_tester/` to `src/riki/`
- Added `pyproject.toml` with `riki` CLI entrypoint
- Added `__main__.py` for `python -m riki` support
- Updated all imports to relative paths within the package
- Updated docs (README, AGENTS.md, CHANGELOG) with new project structure and CLI usage
- Renamed project from `api-contract-tester` to **Riki**