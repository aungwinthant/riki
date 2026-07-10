from __future__ import annotations

import argparse
import json
import time
from typing import Any, Dict, List

from .graph import build_graph
from .models import ContractViolation, ExecutionLog, TestState


async def run_tests(spec_path: str, base_url: str) -> TestState:
    graph = build_graph()

    initial = TestState(
        spec_path=spec_path,
        base_url=base_url,
    )

    config = {"recursion_limit": 200}
    result = await graph.ainvoke(initial.model_dump(), config)
    return TestState(**result)


def generate_markdown_report(state: TestState) -> str:
    lines: List[str] = []
    lines.append("# API Contract Test Report")
    lines.append("")
    lines.append(
        f"- **Spec**: {state.spec_path}"
    )
    lines.append(f"- **Base URL**: {state.base_url}")
    lines.append(f"- **Total Endpoints**: {len(state.endpoints)}")
    lines.append(f"- **Tested Endpoints**: {len(state.results)}")
    total_violations = len(state.violations)
    lines.append(f"- **Contract Violations**: {total_violations}")
    if state.start_time and state.end_time:
        duration = state.end_time - state.start_time
        lines.append(f"- **Duration**: {duration:.2f}s")
    lines.append("")

    passed = sum(
        1 for log in state.results.values() if log.status == "PASS"
    )
    failed = sum(
        1 for log in state.results.values() if log.status == "FAIL"
    )
    errors = sum(
        1 for log in state.results.values() if log.status == "ERROR"
    )
    lines.append("## Summary")
    lines.append("")
    lines.append(f"| Status | Count |")
    lines.append(f"|--------|-------|")
    lines.append(f"| ✅ PASS | {passed} |")
    lines.append(f"| ❌ FAIL | {failed} |")
    lines.append(f"| ⚠️ ERROR | {errors} |")
    lines.append("")

    if state.error:
        lines.append("## Fatal Error")
        lines.append("")
        lines.append(f"```")
        lines.append(state.error)
        lines.append(f"```")
        lines.append("")

    lines.append("## Execution Plan (Topological Order)")
    lines.append("")
    for i, key in enumerate(state.endpoint_queue, 1):
        lines.append(f"{i}. `{key}`")
    lines.append("")

    lines.append("## Reasoning Log")
    lines.append("")
    if state.reasoning_log:
        lines.append("| Step | Endpoint | Action | Decision | Observation |")
        lines.append("|------|----------|--------|----------|-------------|")
        for entry in state.reasoning_log:
            ep = entry.get("endpoint", "")
            action = entry.get("action", "")
            decision = entry.get("decision", "")
            obs = entry.get("observation", "")
            lines.append(f"| {entry.get('step','')} | `{ep}` | {action} | {decision} | {obs} |")
        lines.append("")
    else:
        lines.append("*(no reasoning steps recorded)*")
        lines.append("")

    if state.spec_overrides:
        lines.append("## Spec Overrides")
        lines.append("")
        lines.append("| Endpoint | Field | From | To |")
        lines.append("|----------|-------|------|----|")
        for key, override in sorted(state.spec_overrides.items()):
            field = override.get("field", "")
            frm = override.get("from_type", "")
            to = override.get("to_type", "")
            lines.append(f"| `{key}` | `{field}` | {frm} | {to} |")
        lines.append("")
        lines.append("> Spec overrides are auto-corrections applied at runtime. They are never applied silently — they are always flagged in the report.")
        lines.append("")

    if state.diagnoses:
        lines.append("## Diagnoses (UNKNOWN Violations)")
        lines.append("")
        lines.append("| Endpoint | Status | Action | Reason |")
        lines.append("|----------|--------|--------|--------|")
        for d in state.diagnoses:
            action_emoji = {"skip": "⏭️", "flag_as_bug": "🐛", "suggest_spec_fix": "🔧"}.get(d.action, "❓")
            lines.append(f"| `{d.method}:{d.endpoint}` | {d.violation_type} | {action_emoji} {d.action} | {d.reason} |")
        lines.append("")

    lines.append("## Endpoint Results")
    lines.append("")

    for key, log in state.results.items():
        method, path = key.split(":", 1)
        status_emoji = {"PASS": "✅", "FAIL": "❌", "ERROR": "⚠️", "SKIP": "⏭️"}.get(
            log.status, "❓"
        )
        lines.append(f"### {status_emoji} {method} `{path}`")
        lines.append("")
        lines.append(f"- **Status**: {log.status}")
        lines.append(f"- **HTTP Status**: {log.response_status}")
        lines.append(f"- **Duration**: {log.duration_ms:.1f}ms")
        lines.append(f"- **Retries**: {log.retry_count}")

        if log.request_payload:
            lines.append("")
            lines.append("#### Request Payload")
            lines.append("")
            lines.append(f"```json")
            lines.append(
                json.dumps(log.request_payload, indent=2, default=str)
            )
            lines.append(f"```")

        if log.response_body:
            lines.append("")
            lines.append("#### Response Body")
            lines.append("")
            lines.append(f"```json")
            lines.append(
                json.dumps(log.response_body, indent=2, default=str)
                if isinstance(log.response_body, (dict, list))
                else str(log.response_body)
            )
            lines.append(f"```")

        if log.violations:
            lines.append("")
            lines.append("#### Contract Violations")
            lines.append("")
            lines.append("| # | Type | Field Path | Expected | Actual | Message |")
            lines.append("|---|------|------------|----------|--------|---------|")
            for i, v in enumerate(log.violations, 1):
                vtype = v.violation_type or "?"
                field = v.field_path or "N/A"
                expected = str(v.expected_value or v.expected_status)
                actual = str(v.actual_value or v.actual_status)
                lines.append(
                    f"| {i} | {vtype} | `{field}` | {expected} | {actual} | {v.message} |"
                )
            lines.append("")

        lines.append("---")
        lines.append("")

    lines.append("*Report generated by Riki*")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Autonomous API Contract Testing System"
    )
    parser.add_argument(
        "spec",
        help="Path to OpenAPI spec file (JSON or YAML)",
    )
    parser.add_argument(
        "base_url",
        help="Base URL of the API to test against",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="test_report.md",
        help="Output Markdown report path (default: test_report.md)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Also output results as JSON alongside the Markdown report",
    )

    args = parser.parse_args()

    import asyncio

    start = time.time()
    state = asyncio.run(run_tests(args.spec, args.base_url))
    state.end_time = time.time()

    report = generate_markdown_report(state)

    with open(args.output, "w") as f:
        f.write(report)

    print(f"Report written to {args.output}")
    print(f"Total endpoints: {len(state.endpoints)}")
    print(f"Results: {sum(1 for l in state.results.values() if l.status == 'PASS')} passed, "
          f"{sum(1 for l in state.results.values() if l.status == 'FAIL')} failed, "
          f"{sum(1 for l in state.results.values() if l.status == 'ERROR')} errors")

    if args.json:
        json_path = args.output.replace(".md", ".json")
        with open(json_path, "w") as f:
            json.dump(state.model_dump(), f, indent=2, default=str)
        print(f"JSON report written to {json_path}")


if __name__ == "__main__":
    main()