"""Real environment variables must take priority over .env values.

`settings_customise_sources` documents the priority as env vars > .env > YAML,
but `load_dotenv(override=True)` in common/global_config.py used to stomp the
process environment with .env values at import time, silently inverting it.
Spawns a subprocess because the config singleton is created on first import.
"""

import subprocess
import sys
from pathlib import Path

from tests.test_template import TestTemplate

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestEnvVarPriority(TestTemplate):
    def test_env_var_overrides_dotenv_value(self):
        sentinel = "sqlite:///env-var-priority-sentinel.db"
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from common import global_config; print(global_config.BACKEND_DB_URI)",
            ],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            env={
                "PATH": "/usr/bin:/bin",
                "BACKEND_DB_URI": sentinel,
            },
            check=True,
        )
        assert result.stdout.strip().endswith(sentinel), result.stdout
