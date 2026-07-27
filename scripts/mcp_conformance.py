"""Run MCPJam apps + protocol conformance against the local ``/mcp`` server.

Boots ``api_server.server:app`` on a throwaway SQLite DB, seeds an API key,
runs ``mcpjam apps conformance`` and ``mcpjam protocol conformance`` against the
streamable-HTTP ``/mcp`` mount, and exits non-zero if either run reports a
``failed`` check.

The MCPJam CLI exits 0 even when checks fail, so gating is done by parsing the
JSON report (``failed`` count), not the process exit code.

Used by ``make mcp_conformance`` and the ``mcp_conformance`` CI workflow.
Requires Node (``npx``) on PATH; the MCPJam version is pinned for reproducibility.

Usage::

    uv run python scripts/mcp_conformance.py            # gate: fail on any failed check
    uv run python scripts/mcp_conformance.py --report   # print results, never fail
"""

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HOST = "127.0.0.1"


def _free_port() -> int:
    """Pick an OS-assigned free TCP port to avoid collisions across runs."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((HOST, 0))
        return sock.getsockname()[1]


# An explicit port wins; otherwise grab a free one so concurrent runs don't clash.
PORT = int(os.environ.get("MCP_CONFORMANCE_PORT") or _free_port())
MCPJAM_VERSION = os.environ.get("MCPJAM_VERSION", "3.4.0")
READY_TIMEOUT_S = int(os.environ.get("MCP_CONFORMANCE_READY_TIMEOUT", "90"))
CMD_TIMEOUT_S = int(os.environ.get("MCP_CONFORMANCE_CMD_TIMEOUT", "300"))
RESULTS_DIR = Path(
    os.environ.get("MCP_CONFORMANCE_RESULTS_DIR", "mcp_conformance_results")
)

# Checks that fail for reasons unrelated to server correctness in CI and are
# tracked as known-acceptable. Keyed by "<suite>:<check-id>". Empty by default;
# populated only after the real-server baseline is reviewed.
ALLOWED_FAILURES: set[str] = set()


def _configure_db_env() -> None:
    """Point the app at a throwaway file SQLite shared by seed + server.

    The filename is PID-scoped so concurrent conformance runs on one host
    don't clobber each other's database.
    """
    db_path = Path(tempfile.gettempdir()) / f"mcp_conformance_{os.getpid()}.db"
    if db_path.exists():
        db_path.unlink()
    os.environ["BACKEND_DB_URI"] = f"sqlite:///{db_path}"
    os.environ.setdefault("DEV_ENV", "dev")


def _seed_api_key() -> str:
    """Create tables and a scoped API key; return the raw key."""
    # Imported after _configure_db_env so config/engine pick up BACKEND_DB_URI;
    # api_server.server registers every ORM model on Base.metadata.
    import api_server.server  # noqa: F401, PLC0415
    from api_server.auth.api_key_auth import create_api_key  # noqa: PLC0415
    from db.base import Base  # noqa: PLC0415
    from db.engine import _init_engine, use_db_session  # noqa: PLC0415

    Base.metadata.create_all(_init_engine())
    with use_db_session() as session:
        raw_key, _ = create_api_key(
            session, user_id="conformance-ci", name="conformance", scopes=["*"]
        )
    return raw_key


def _start_server() -> subprocess.Popen:
    """Launch uvicorn in a subprocess sharing this process's env."""
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "api_server.server:app",
            "--host",
            HOST,
            "--port",
            str(PORT),
            "--log-level",
            "warning",
        ],
        env=os.environ.copy(),
    )


def _wait_ready(proc: subprocess.Popen) -> None:
    """Poll /health until the server answers 200 or the timeout elapses."""
    url = f"http://{HOST}:{PORT}/health"
    deadline = time.monotonic() + READY_TIMEOUT_S
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"server exited early (code {proc.returncode})")
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(0.5)
    raise TimeoutError(f"server not ready on {url} after {READY_TIMEOUT_S}s")


def _run_conformance(suite: str, api_key: str) -> dict:
    """Run one conformance suite and return its parsed JSON report."""
    cmd = [
        "npx",
        "-y",
        f"@mcpjam/cli@{MCPJAM_VERSION}",
        "--format",
        "json",
        suite,
        "conformance",
        "--url",
        f"http://{HOST}:{PORT}/mcp",
        "--header",
        f"X-API-KEY: {api_key}",
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=CMD_TIMEOUT_S
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"{suite}: conformance timed out after {CMD_TIMEOUT_S}s"
        ) from exc
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / f"{suite}-conformance.json").write_text(proc.stdout)
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        sys.stderr.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        raise RuntimeError(f"{suite}: could not parse MCPJam JSON output") from exc


def _print_report(suite: str, report: dict) -> list[str]:
    """Print a per-check table; return keys of non-allowed failed checks."""
    print(f"\n=== {suite} conformance: {report.get('summary', '?')} ===")
    blocking: list[str] = []
    for check in report.get("checks", []):
        status = check.get("status")
        key = f"{suite}:{check.get('id')}"
        mark = {"passed": "✅", "skipped": "⏭️ ", "failed": "❌"}.get(status, "? ")
        line = f"  {mark} {check.get('id')} [{check.get('category')}]"
        if status == "failed":
            err = (check.get("error") or {}).get("message", "")
            line += f": {err}"
            if key in ALLOWED_FAILURES:
                line += "  (allowed)"
            else:
                blocking.append(key)
        print(line)
    return blocking


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--report",
        action="store_true",
        help="Print results but always exit 0 (used to capture the baseline).",
    )
    args = parser.parse_args()

    _configure_db_env()
    api_key = _seed_api_key()
    server = _start_server()
    blocking: list[str] = []
    try:
        _wait_ready(server)
        for suite in ("protocol", "apps"):
            report = _run_conformance(suite, api_key)
            blocking += _print_report(suite, report)
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()

    if args.report:
        print(f"\n[report mode] {len(blocking)} blocking failure(s) would gate CI.")
        return 0
    if blocking:
        print(f"\n❌ {len(blocking)} conformance failure(s): {', '.join(blocking)}")
        return 1
    print("\n✅ MCP conformance passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
