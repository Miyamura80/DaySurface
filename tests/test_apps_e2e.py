"""L4 rendered-iframe e2e (real Goose desktop) as a guarded pytest entry.

This is the pytest face of ``make test_apps_e2e``: it makes the heavy L4 tier a
first-class, collectable member of the suite while staying **skipped by default**
so ``make ci`` / ``make test`` remain fast and free of Node/Rust/Electron.

Opt in with ``RUN_APPS_E2E=1`` once the Goose desktop is built
(``bash .agents/skills/goose-gui-e2e/scripts/setup.sh``). The body shells out to
the harness, which drives the real Electron host and asserts the ``ui://`` app
render + the click -> ``callServerTool`` -> re-render round-trip. The scripts own
all the assertions; a non-zero exit fails this test.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests.test_template import slow_test

_HARNESS = Path(__file__).resolve().parents[1] / ".agents/skills/goose-gui-e2e/scripts"
_GOOSE_BIN = Path.home() / "goose-e2e/goose_src/target/debug/goose"

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_APPS_E2E") != "1",
    reason="L4 Goose-GUI e2e is opt-in; set RUN_APPS_E2E=1 (needs a built Goose desktop)",
)


@slow_test
def test_apps_e2e_scenarios():
    """Bring the stack up and run every scenario against the real Goose GUI."""
    if not _GOOSE_BIN.exists():
        pytest.skip(f"Goose not built at {_GOOSE_BIN}; run {_HARNESS}/setup.sh first")
    subprocess.run(["bash", str(_HARNESS / "up.sh")], check=True)
    subprocess.run(["bash", str(_HARNESS / "run_all.sh")], check=True)
