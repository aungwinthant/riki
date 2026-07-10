from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, Any

from .conftest import BASELINE_DIR

BASELINE_FILE = BASELINE_DIR / "report.json"
SPEC_PATH = Path(__file__).parent.parent / "src" / "riki" / "sample_spec.yaml"
RIKI_BIN = "riki"


def _run_riki_test(base_url: str, spec_path: str, cwd: Path) -> Dict[str, Any]:
    """Run riki test and return the JSON report."""
    result = subprocess.run(
        [RIKI_BIN, "test", "--base-url", base_url, "--spec", spec_path],
        capture_output=True,
        text=True,
        cwd=str(cwd),
    )
    report_path = cwd / ".riki" / "test_report.json"
    if not report_path.exists():
        raise RuntimeError(
            f"No report generated.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    with open(report_path) as f:
        return json.load(f)


def _normalise_report(report: Dict[str, Any]) -> Dict[str, Any]:
    """Strip runtime-variant fields for deterministic comparison."""
    stripped = {}
    for key, val in report.items():
        if key in ("start_time", "end_time", "raw_spec", "spec_path"):
            continue
        if key == "results":
            stripped[key] = {}
            for ek, ev in val.items():
                stripped[key][ek] = {
                    k: v for k, v in ev.items() if k not in ("duration_ms",)
                }
        elif key in ("payloads",):
            stripped[key] = val
        elif key == "memory":
            stripped[key] = val
        elif key == "violations":
            stripped[key] = val
        elif key == "endpoints":
            stripped[key] = [
                {k: v for k, v in ep.items() if k != "operation_id"}
                for ep in val
            ]
        elif key == "retry_map":
            stripped[key] = val
        elif key == "error":
            stripped[key] = val
        else:
            stripped[key] = val
    return stripped


def test_generate_baseline(mock_server: str, tmp_path: Path):
    """Generate the golden baseline report."""
    report = _run_riki_test(mock_server, str(SPEC_PATH), tmp_path)
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    with open(BASELINE_FILE, "w") as f:
        json.dump(_normalise_report(report), f, indent=2)
    print(f"Baseline written to {BASELINE_FILE}")


def test_check_baseline(mock_server: str, tmp_path: Path):
    """Check current output against golden baseline."""
    if not BASELINE_FILE.exists():
        pytest.skip("No baseline file found — run with --run-baseline-update first")

    with open(BASELINE_FILE) as f:
        baseline = json.load(f)

    report = _run_riki_test(mock_server, str(SPEC_PATH), tmp_path)
    current = _normalise_report(report)

    assert current == baseline, (
        f"Output differs from baseline.\n"
        f"Run with --run-baseline-update to regenerate.\n"
        f"Diff keys added: {set(current) - set(baseline)}\n"
        f"Diff keys removed: {set(baseline) - set(current)}\n"
    )

    passed = sum(1 for r in current.get("results", {}).values() if r.get("status") == "PASS")
    failed = sum(1 for r in current.get("results", {}).values() if r.get("status") == "FAIL")
    print(f"Baseline check passed: {passed} PASS, {failed} FAIL")
