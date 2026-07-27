"""Anonymous telemetry - local JSON, opt-out, first-run notice."""

import hashlib
import importlib.metadata
import json
import os
import platform
import socket
import threading
from datetime import UTC, datetime

import typer
from rich.console import Console

from src.cli.state_store import _CONFIG_DIR, load_state, save_state

app = typer.Typer(no_args_is_help=True)
console = Console(stderr=True)

_TELEMETRY_FILE = _CONFIG_DIR / "telemetry.json"
_MAX_EVENTS = 1000


def _machine_id() -> str:
    """Anonymous machine ID: truncated SHA-256 hash of hostname."""
    raw = socket.gethostname()
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def is_enabled() -> bool:
    """Check if telemetry is enabled."""
    if os.environ.get("CLI_TELEMETRY_DISABLED", "").strip() in ("1", "true", "yes"):
        return False
    state = load_state()
    return state.get("telemetry_enabled", True)


def show_first_run_notice() -> None:
    """Print a one-time telemetry notice."""
    state = load_state()
    if state.get("telemetry_notice_shown"):
        return
    if is_enabled():
        console.print(
            "[dim]Anonymous usage telemetry is enabled. "
            "Run 'mymcp telemetry disable' or set CLI_TELEMETRY_DISABLED=1 to opt out.[/dim]"
        )
    state["telemetry_notice_shown"] = True
    save_state(state)


def record_event(command: str, duration: float, success: bool) -> None:
    """Record a telemetry event to the local JSON file."""
    if not is_enabled():
        return

    event = {
        "command": command,
        "duration_s": round(duration, 3),
        "success": success,
        "cli_version": importlib.metadata.version("mcp-template"),
        "python_version": platform.python_version(),
        "os": platform.system(),
        "machine_id": _machine_id(),
        "timestamp": datetime.now(UTC).isoformat(),
    }

    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    events: list[dict] = []
    if _TELEMETRY_FILE.exists():
        try:
            events = json.loads(_TELEMETRY_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            events = []

    events.append(event)
    # Cap at _MAX_EVENTS
    if len(events) > _MAX_EVENTS:
        events = events[-_MAX_EVENTS:]

    _TELEMETRY_FILE.write_text(json.dumps(events, indent=2))

    # POST to configured endpoint if set
    _post_event(event)


def _get_endpoint() -> str | None:
    """Read the telemetry endpoint from global config, if configured."""
    try:
        # Lazy by design: importing `common` triggers the full pydantic-settings
        # config load; keep that off the CLI startup path. ImportError is
        # handled below so a missing/broken config never breaks telemetry.
        from common import global_config  # noqa: PLC0415

        ep = global_config.telemetry.endpoint
        return ep if ep else None
    except (ImportError, AttributeError):
        return None


def _post_event(event: dict) -> None:
    """POST a single event to the configured telemetry endpoint (best-effort, non-blocking)."""
    endpoint = _get_endpoint()
    if not endpoint:
        return

    def _send() -> None:
        try:
            # Lazy by design: urllib.request is slow to import and only needed
            # when a telemetry endpoint is configured; keep it off CLI startup.
            import urllib.request  # noqa: PLC0415

            data = json.dumps(event).encode()
            req = urllib.request.Request(
                endpoint,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=2)  # noqa: S310
        except Exception:  # noqa: BLE001
            # Daemon thread: any failure (network, encoding, import) must be swallowed
            # so telemetry never crashes the CLI process.
            pass

    threading.Thread(target=_send, daemon=True).start()


@app.command()
def status() -> None:
    """Show telemetry status."""
    enabled = is_enabled()
    state = "enabled" if enabled else "disabled"
    console.print(f"Telemetry is [bold]{state}[/bold]")
    if _TELEMETRY_FILE.exists():
        try:
            events = json.loads(_TELEMETRY_FILE.read_text())
            console.print(f"Local events recorded: {len(events)}")
        except (json.JSONDecodeError, OSError):
            pass


@app.command()
def enable() -> None:
    """Enable anonymous telemetry."""
    state = load_state()
    state["telemetry_enabled"] = True
    save_state(state)
    console.print("[green]Telemetry enabled.[/green]")


@app.command()
def disable() -> None:
    """Disable anonymous telemetry."""
    state = load_state()
    state["telemetry_enabled"] = False
    save_state(state)
    console.print("[yellow]Telemetry disabled.[/yellow]")
