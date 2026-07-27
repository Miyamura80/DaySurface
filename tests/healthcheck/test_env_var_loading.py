import importlib
import os
import sys
from pathlib import Path

# Get the path to the root directory
root_dir = Path(__file__).parent.parent.parent


def test_env_var_loading_precedence(monkeypatch):
    """
    Test that environment variables are loaded with the correct precedence:
    system environment variables > .env file (the order documented in
    Config.settings_customise_sources). A real env var must never be
    silently overridden by a .env value, or per-process overrides
    (CI, Docker, `VAR=... uv run ...`) stop working.
    """
    dot_env_path = root_dir / ".env"
    original_dot_env_content = None
    if dot_env_path.exists():
        with open(dot_env_path, "r") as f:
            original_dot_env_content = f.read()

    common_module = sys.modules["common.global_config"]

    try:
        # 1. Set mock system environment variables. Every key written to the
        # temporary .env below is also set here, so load_dotenv never writes
        # into os.environ and monkeypatch.undo() restores it fully.
        monkeypatch.setenv("DEV_ENV", "system")
        monkeypatch.setenv("OPENAI_API_KEY", "system_openai_key")
        # This one is not in the .env file, so it is loaded from the system env
        monkeypatch.setenv("ANTHROPIC_API_KEY", "system_anthropic_key")

        # 2. Create a temporary .env file that conflicts with the system env
        dot_env_content = "DEV_ENV=dotenv\nOPENAI_API_KEY=dotenv_openai_key\n"
        with open(dot_env_path, "w") as f:
            f.write(dot_env_content)

        # 3. Reload the common module to pick up the new .env file
        importlib.reload(common_module)  # noqa: TID251 - test fixture: re-evaluate config with patched env
        reloaded_config = common_module.global_config

        # 4. Assert that the variables are loaded with the correct precedence
        assert reloaded_config.DEV_ENV == "system", "System env should beat .env"
        assert reloaded_config.OPENAI_API_KEY == "system_openai_key", (
            "System env should beat .env"
        )
        assert reloaded_config.ANTHROPIC_API_KEY == "system_anthropic_key", (
            "System-env-only variables should load"
        )

    finally:
        # Clean up and restore the original .env file if it existed
        if original_dot_env_content is not None:
            with open(dot_env_path, "w") as f:
                f.write(original_dot_env_content)
        else:
            if os.path.exists(dot_env_path):
                os.remove(dot_env_path)

        # Drop the monkeypatched env vars *before* the restore reload --
        # system env now beats .env, so a reload with them still set would
        # leak the mock values into the restored global_config.
        monkeypatch.undo()
        importlib.reload(common_module)  # noqa: TID251 - test fixture: restore original config
