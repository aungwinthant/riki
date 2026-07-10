from __future__ import annotations

import multiprocessing
import os
import time
from pathlib import Path
from typing import Generator

import pytest

MOCK_PORT = 18765
BASELINE_DIR = Path(__file__).parent / "baseline"


def _start_mock_server():
    """Run mock server in a subprocess."""
    os.environ["Riki_AUTH"] = ""
    from riki.mock_server import run_mock_server

    run_mock_server(port=MOCK_PORT)


@pytest.fixture(scope="session")
def mock_server(request: pytest.FixtureRequest) -> Generator[str, None, None]:
    """Start the mock server and yield its base URL."""
    proc = multiprocessing.Process(target=_start_mock_server, daemon=True)
    proc.start()
    time.sleep(0.5)
    yield f"http://localhost:{MOCK_PORT}"
    proc.terminate()
    proc.join()


@pytest.fixture
def spec_path() -> Path:
    return Path(__file__).parent.parent / "src" / "riki" / "sample_spec.yaml"
