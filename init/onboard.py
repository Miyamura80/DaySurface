"""Interactive onboarding CLI for project setup."""

import asyncio
import json
import os
import random
import re
import shutil
import subprocess
import tomllib
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

import questionary
import typer
import yaml
from rich import print as rprint
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Branding configuration -------------------------------------------------------

#: (name, primary_color, secondary_color, description)
COLOR_PALETTES: list[tuple[str, str, str, str]] = [
    ("Ocean", "bright_cyan", "blue", "Cool blues and teals"),
    ("Forest", "bright_green", "green", "Natural greens"),
    ("Sunset", "yellow", "bright_red", "Warm and fiery"),
    ("Aurora", "bright_magenta", "bright_cyan", "Vibrant purples and teals"),
    ("Rose", "bright_red", "magenta", "Warm pinks and reds"),
    ("Gold", "bright_yellow", "yellow", "Rich golden tones"),
    ("Slate", "bright_white", "cyan", "Clean whites with cyan"),
    ("Midnight", "bright_blue", "blue", "Deep ocean blues"),
]

PRESET_EMOJIS: list[str] = [
    "🚀",
    "⚡",
    "🔥",
    "🛠️",
    "🎯",
    "✨",
    "🌟",
    "💎",
    "🦊",
    "🐉",
    "🌊",
    "🌿",
    "🔮",
    "🧪",
    "🎨",
    "🤖",
]

# ------------------------------------------------------------------------------

app = typer.Typer(
    name="onboard",
    help="Interactive onboarding CLI for project setup.",
    invoke_without_command=True,
)


def _read_pyproject_name() -> str:
    """Read the current project name from pyproject.toml."""
    text = (PROJECT_ROOT / "pyproject.toml").read_text()
    match = re.search(r'^name\s*=\s*"([^"]*)"', text, re.MULTILINE)
    return match.group(1) if match else ""


def _validate_kebab_case(value: str) -> bool | str:
    """Validate that the value is kebab-case (lowercase, hyphens, no spaces)."""
    if not value:
        return "Project name cannot be empty."
    if not re.match(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$", value):
        return "Must be kebab-case (e.g. my-cool-project). Lowercase letters, digits, hyphens only."
    return True


def _validate_cli_name(value: str) -> bool | str:
    """Validate that the value is a valid CLI command name."""
    if not value:
        return "CLI name cannot be empty."
    if not re.match(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$", value):
        return "Must be lowercase with optional hyphens (e.g. my-tool). No spaces or underscores."
    return True


def _read_cli_name() -> str:
    """Read the current CLI entry-point name from pyproject.toml [project.scripts]."""
    text = (PROJECT_ROOT / "pyproject.toml").read_text()
    match = re.search(r"^\[project\.scripts\]\s*\n(\S+)\s*=", text, re.MULTILINE)
    return match.group(1) if match else "mymcp"


STEPS: list[tuple[str, str]] = [
    ("Branding", "branding"),
    ("Rename", "rename"),
    ("CLI Name", "cli_name"),
    ("Dependencies", "deps"),
    ("Environment Variables", "env"),
    ("Pre-commit Hooks", "hooks"),
    ("MCP Server", "mcp"),
    ("Media Generation", "media"),
    ("Jules Workflows", "jules"),
]

STEP_FUNCTIONS: dict[str, object] = {}


class ServiceSurface(StrEnum):
    """Transports that can expose the shared service registry."""

    CLI = "cli"
    MCP = "mcp"
    HTTP_API = "http_api"
    MCP_APPS = "mcp_apps"


class PaymentStack(StrEnum):
    """Payment stacks the template can keep or prune."""

    STRIPE_BILLING = "stripe_billing"
    STRIPE_METERING = "stripe_metering"
    X402 = "x402"
    MPP_STUB = "mpp_stub"
    ACP_STUB = "acp_stub"


class ExampleApp(StrEnum):
    """Example integrations that are useful references but not core template code."""

    GMAIL_GOOGLE_OAUTH = "gmail_google_oauth"
    AGENTIC_PAYMENT_RESEARCH_DOCS = "agentic_payment_research_docs"


class OnboardingProfile(StrEnum):
    """Preset project shapes. Users can override any field headlessly."""

    CLI_ONLY = "cli-only"
    LOCAL_MCP = "local-mcp"
    REMOTE_MCP = "remote-mcp"
    FULL_SAAS = "full-saas"
    CUSTOM = "custom"


@dataclass(frozen=True)
class OnboardingConfig:
    """Headless onboarding decisions.

    This is intentionally stdlib-only so the setup path stays light and can be
    reused by tests, the interactive wizard, and agent-driven onboarding.
    """

    profile: OnboardingProfile = OnboardingProfile.CUSTOM
    service_surfaces: frozenset[ServiceSurface] = field(default_factory=frozenset)
    payments: frozenset[PaymentStack] = field(default_factory=frozenset)
    examples: frozenset[ExampleApp] = field(default_factory=frozenset)
    auth: bool = False
    database: bool = False
    docs: bool = False

    @classmethod
    def for_profile(cls, profile: OnboardingProfile) -> "OnboardingConfig":
        """Return defaults for a project-shape preset."""
        defaults = {
            OnboardingProfile.CLI_ONLY: cls(
                profile=profile,
                service_surfaces=frozenset({ServiceSurface.CLI}),
            ),
            OnboardingProfile.LOCAL_MCP: cls(
                profile=profile,
                service_surfaces=frozenset({ServiceSurface.CLI, ServiceSurface.MCP}),
            ),
            OnboardingProfile.REMOTE_MCP: cls(
                profile=profile,
                service_surfaces=frozenset(
                    {ServiceSurface.CLI, ServiceSurface.MCP, ServiceSurface.HTTP_API}
                ),
                auth=True,
                database=True,
                docs=True,
            ),
            OnboardingProfile.FULL_SAAS: cls(
                profile=profile,
                service_surfaces=frozenset(
                    {
                        ServiceSurface.CLI,
                        ServiceSurface.MCP,
                        ServiceSurface.HTTP_API,
                        ServiceSurface.MCP_APPS,
                    }
                ),
                payments=frozenset(
                    {PaymentStack.STRIPE_BILLING, PaymentStack.STRIPE_METERING}
                ),
                auth=True,
                database=True,
                docs=True,
            ),
            OnboardingProfile.CUSTOM: cls(profile=profile),
        }
        return defaults[profile]

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "OnboardingConfig":
        """Build config from a YAML/JSON mapping."""
        profile = _coerce_choice(
            data.get("profile", OnboardingProfile.CUSTOM.value), OnboardingProfile
        )
        base = cls.for_profile(profile)

        surfaces = data.get("service_surfaces", data.get("surfaces"))
        payments = data.get("payments")
        examples = data.get("examples")

        return cls(
            profile=profile,
            service_surfaces=_coerce_choice_set(
                surfaces, ServiceSurface, default=base.service_surfaces
            ),
            payments=_coerce_choice_set(payments, PaymentStack, default=base.payments),
            examples=_coerce_choice_set(examples, ExampleApp, default=base.examples),
            auth=bool(data.get("auth", base.auth)),
            database=bool(data.get("database", base.database)),
            docs=bool(data.get("docs", base.docs)),
        ).expanded()

    def with_overrides(
        self,
        *,
        service_surfaces: frozenset[ServiceSurface] | None = None,
        payments: frozenset[PaymentStack] | None = None,
        examples: frozenset[ExampleApp] | None = None,
        auth: bool | None = None,
        database: bool | None = None,
        docs: bool | None = None,
    ) -> "OnboardingConfig":
        """Return a copy with explicit CLI overrides applied."""
        return OnboardingConfig(
            profile=self.profile,
            service_surfaces=(
                self.service_surfaces if service_surfaces is None else service_surfaces
            ),
            payments=self.payments if payments is None else payments,
            examples=self.examples if examples is None else examples,
            auth=self.auth if auth is None else auth,
            database=self.database if database is None else database,
            docs=self.docs if docs is None else docs,
        ).expanded()

    def expanded(self) -> "OnboardingConfig":
        """Apply dependency implications from selected stacks to project shape."""
        surfaces = set(self.service_surfaces)
        payments = set(self.payments)
        examples = set(self.examples)
        auth = self.auth
        database = self.database
        docs = self.docs

        if PaymentStack.STRIPE_METERING in payments:
            payments.add(PaymentStack.STRIPE_BILLING)

        if PaymentStack.STRIPE_BILLING in payments:
            surfaces.add(ServiceSurface.HTTP_API)
            auth = True
            database = True

        if PaymentStack.X402 in payments:
            # Current agentic payment routes are HTTP API routes protected by scopes.
            surfaces.add(ServiceSurface.HTTP_API)
            auth = True

        if ExampleApp.GMAIL_GOOGLE_OAUTH in examples:
            surfaces.update(
                {ServiceSurface.HTTP_API, ServiceSurface.MCP, ServiceSurface.MCP_APPS}
            )
            auth = True
            database = True

        if ServiceSurface.MCP_APPS in surfaces:
            surfaces.add(ServiceSurface.MCP)

        if auth:
            database = True

        if self.profile is OnboardingProfile.FULL_SAAS:
            docs = True

        return OnboardingConfig(
            profile=self.profile,
            service_surfaces=frozenset(surfaces),
            payments=frozenset(payments),
            examples=frozenset(examples),
            auth=auth,
            database=database,
            docs=docs,
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a stable, serializable representation."""
        return {
            "profile": self.profile.value,
            "service_surfaces": sorted(item.value for item in self.service_surfaces),
            "payments": sorted(item.value for item in self.payments),
            "examples": sorted(item.value for item in self.examples),
            "auth": self.auth,
            "database": self.database,
            "docs": self.docs,
        }


def _choice_values(enum_type: type[StrEnum]) -> str:
    return ", ".join(item.value for item in enum_type)


def _coerce_choice(value: Any, enum_type: type[StrEnum]):
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(str(value))
    except ValueError as exc:
        allowed = _choice_values(enum_type)
        raise typer.BadParameter(
            f"Invalid value {value!r}. Expected one of: {allowed}"
        ) from exc


def _coerce_choice_set(
    value: Any,
    enum_type: type[StrEnum],
    *,
    default: frozenset | None = None,
) -> frozenset:
    """Parse comma-separated CLI values or YAML lists into enum sets."""
    if value is None:
        return frozenset() if default is None else default
    if value == "":
        return frozenset()
    if isinstance(value, str):
        raw_items = [item.strip() for item in value.split(",") if item.strip()]
    elif isinstance(value, list | tuple | set | frozenset):
        raw_items = list(value)
    else:
        raise typer.BadParameter(
            f"Expected a comma-separated string or list, got {type(value).__name__}"
        )
    return frozenset(_coerce_choice(item, enum_type) for item in raw_items)


def load_onboarding_config(path: Path) -> OnboardingConfig:
    """Load a headless onboarding config from YAML or JSON."""
    try:
        text = path.read_text()
    except FileNotFoundError as exc:
        raise typer.BadParameter(f"Config file not found: {path}") from exc

    try:
        if path.suffix == ".json":
            data = json.loads(text)
        else:
            data = yaml.safe_load(text) or {}
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise typer.BadParameter(f"Invalid onboarding config: {exc}") from exc

    if not isinstance(data, dict):
        raise typer.BadParameter("Onboarding config must be a mapping.")
    return OnboardingConfig.from_mapping(data)


def plan_onboarding(config: OnboardingConfig) -> list[str]:
    """Return high-level actions implied by a headless config."""
    actions = ["Apply deterministic template rename/branding/setup inputs."]

    if ServiceSurface.CLI in config.service_surfaces:
        actions.append("Keep CLI command surface.")
    else:
        actions.append("Prune CLI command surface.")

    if ServiceSurface.MCP in config.service_surfaces:
        actions.append("Keep MCP tool surface.")
    else:
        actions.append("Prune MCP server files and distribution metadata.")

    if ServiceSurface.HTTP_API in config.service_surfaces:
        actions.append("Keep HTTP API service routes.")
    else:
        actions.append("Prune FastAPI server and API-only routes.")

    if ServiceSurface.MCP_APPS in config.service_surfaces:
        actions.append("Keep MCP App/enhancer surface.")
    else:
        actions.append("Prune MCP App example bundles and enhancers.")

    actions.append(
        "Keep auth infrastructure." if config.auth else "Prune auth infrastructure."
    )
    actions.append(
        "Keep DB infrastructure." if config.database else "Prune DB infrastructure."
    )
    actions.append("Keep docs site." if config.docs else "Prune docs site.")

    if config.payments:
        enabled = ", ".join(sorted(item.value for item in config.payments))
        actions.append(f"Keep payment stacks: {enabled}.")
    else:
        actions.append("Prune all payment stacks.")

    if config.examples:
        enabled = ", ".join(sorted(item.value for item in config.examples))
        actions.append(f"Keep example apps/docs: {enabled}.")
    else:
        actions.append("Prune example apps/docs, including Gmail/Google OAuth.")

    return actions


def _remove_path(path: Path) -> bool:
    """Remove a file, symlink, or directory if it exists."""
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
        return True
    if path.exists() or path.is_symlink():
        path.unlink()
        return True
    return False


def _remove_paths(paths: list[str]) -> list[str]:
    """Remove repo-relative paths and return the removed path list."""
    removed: list[str] = []
    for rel_path in paths:
        if _remove_path(PROJECT_ROOT / rel_path):
            removed.append(rel_path)
    return removed


def _remove_lines_matching(path: Path, patterns: list[str]) -> bool:
    """Remove lines containing any pattern from a text file."""
    if not path.exists():
        return False
    lines = path.read_text().splitlines()
    kept = [line for line in lines if not any(pattern in line for pattern in patterns)]
    if kept == lines:
        return False
    path.write_text("\n".join(kept) + "\n")
    return True


def _rewrite_pyproject_list(
    text: str,
    field_name: str,
    remove_values: set[str],
) -> str:
    """Rewrite a single-line TOML string list by removing exact values."""
    pattern = rf"({field_name}\s*=\s*)\[([^\]]*)\]"
    match = re.search(pattern, text)
    if not match:
        return text

    values = re.findall(r'"([^"]+)"', match.group(2))
    kept = [value for value in values if value not in remove_values]
    replacement = f"{match.group(1)}[{', '.join(f'"{value}"' for value in kept)}]"
    return text[: match.start()] + replacement + text[match.end() :]


def _rewrite_pyproject_for_config(config: OnboardingConfig) -> list[str]:
    """Remove pyproject references for pruned onboarding surfaces."""
    pyproject = PROJECT_ROOT / "pyproject.toml"
    text = pyproject.read_text()
    original = text
    changes: list[str] = []

    dependency_patterns: list[str] = []
    packages_to_remove: set[str] = set()
    scripts_to_remove: list[str] = []
    vulture_patterns: list[str] = []
    rules = [
        (
            ServiceSurface.CLI not in config.service_surfaces,
            ['"keyring'],
            set(),
            ["src.cli.app:main_cli"],
            ['"src/cli/', '"tests/cli/'],
        ),
        (
            ServiceSurface.MCP not in config.service_surfaces,
            ['"mcp[cli]'],
            {"mcp_server"},
            ["mcp_server:main"],
            ['"mcp_server/', '"tests/test_mcp_'],
        ),
        (
            ServiceSurface.HTTP_API not in config.service_surfaces,
            [
                '"fastapi',
                '"uvicorn',
                '"starlette',
                '"limits',
                '"redis',
                '"itsdangerous',
            ],
            {"api_server"},
            ["api_server:main"],
            ['"api_server/', '"tests/test_api_'],
        ),
        (
            not config.database,
            ['"sqlalchemy', '"alembic', '"psycopg2-binary'],
            {"db"},
            [],
            ['"db/', '"tests/test_db_'],
        ),
        (
            not config.auth,
            ['"PyJWT', '"cryptography'],
            set(),
            [],
            [
                '"tests/test_api_key_auth.py"',
                '"tests/test_unified_auth.py"',
                '"tests/test_workos_auth.py"',
                '"tests/test_scopes.py"',
            ],
        ),
        (
            PaymentStack.STRIPE_BILLING not in config.payments,
            ['"stripe'],
            set(),
            [],
            ['"src/payments/', '"tests/test_billing.py"'],
        ),
        (
            PaymentStack.X402 not in config.payments,
            ['"x402'],
            set(),
            [],
            [
                '"tests/test_agentic_payments.py"',
                '"tests/test_agentic_payments_api.py"',
            ],
        ),
    ]
    for enabled, deps, packages, scripts, vulture in rules:
        if not enabled:
            continue
        dependency_patterns.extend(deps)
        packages_to_remove.update(packages)
        scripts_to_remove.extend(scripts)
        vulture_patterns.extend(vulture)

    for pattern in dependency_patterns:
        text = re.sub(rf"^\s*{re.escape(pattern)}.*\n", "", text, flags=re.MULTILINE)
    for script_target in scripts_to_remove:
        text = re.sub(
            rf"^.*=\s*\"{re.escape(script_target)}\"\s*\n",
            "",
            text,
            flags=re.MULTILINE,
        )
    text = _rewrite_pyproject_list(text, "packages", packages_to_remove)
    text = _rewrite_pyproject_list(text, "source", packages_to_remove)
    if ServiceSurface.MCP_APPS not in config.service_surfaces:
        text = re.sub(r"force-include\s*=\s*\{[^}]*\}", "force-include = {}", text)
    for pattern in vulture_patterns:
        text = re.sub(rf"^\s*{re.escape(pattern)}.*\n", "", text, flags=re.MULTILINE)

    if text != original:
        pyproject.write_text(text)
        changes.append("pyproject.toml")
    return changes


def _remove_make_target_blocks(targets: set[str]) -> bool:
    """Remove simple Makefile target blocks."""
    path = PROJECT_ROOT / "Makefile"
    lines = path.read_text().splitlines()
    out: list[str] = []
    skipping = False

    target_re = re.compile(r"^([a-zA-Z0-9_-]+):")
    for line in lines:
        match = target_re.match(line)
        if match and match.group(1) in targets:
            skipping = True
            continue
        if skipping:
            if line.startswith("\t") or line.strip() == "":
                continue
            skipping = False
        out.append(line)

    if out == lines:
        return False
    path.write_text("\n".join(out) + "\n")
    return True


def _rewrite_importlinter_for_config(config: OnboardingConfig) -> list[str]:
    """Remove root package references for pruned top-level packages."""
    removed_modules: list[str] = []
    if ServiceSurface.MCP not in config.service_surfaces:
        removed_modules.append("mcp_server")
    if ServiceSurface.HTTP_API not in config.service_surfaces:
        removed_modules.append("api_server")
    if not config.database:
        removed_modules.append("db")
    if not removed_modules:
        return []
    path = PROJECT_ROOT / ".importlinter"
    if _remove_lines_matching(path, removed_modules):
        return [".importlinter"]
    return []


def _rewrite_api_server_for_config(config: OnboardingConfig) -> list[str]:
    """Remove API route registrations for pruned optional systems."""
    server_path = PROJECT_ROOT / "api_server" / "server.py"
    if not server_path.exists():
        return []
    text = server_path.read_text()
    original = text

    if not config.payments:
        text = text.replace("agentic_payments, ", "")
        text = text.replace(", agentic_payments", "")
        text = re.sub(
            r"^from api_server\.routes\.payments import .*\n",
            "",
            text,
            flags=re.MULTILINE,
        )
        text = re.sub(
            r"^app\.include_router\((agentic_payments|checkout|metering|subscription|webhooks)\.router\)\n",
            "",
            text,
            flags=re.MULTILINE,
        )

    if ExampleApp.GMAIL_GOOGLE_OAUTH not in config.examples:
        text = text.replace("google_oauth, ", "")
        text = text.replace(", google_oauth", "")
        text = re.sub(
            r"^app\.include_router\(google_oauth\.router\)\n",
            "",
            text,
            flags=re.MULTILINE,
        )

    if text == original:
        return []
    server_path.write_text(text)
    return ["api_server/server.py"]


def _rewrite_mcp_server_for_config(config: OnboardingConfig) -> list[str]:
    """Remove API-auth coupling from MCP when the HTTP API is pruned."""
    path = PROJECT_ROOT / "mcp_server" / "_tool_factory.py"
    if ServiceSurface.MCP not in config.service_surfaces or not path.exists():
        return []
    if ServiceSurface.HTTP_API in config.service_surfaces:
        return []

    text = path.read_text()
    original = text
    replacement = '''def _check_scopes() -> None:
    """No-op for local MCP profiles without HTTP auth."""
    return


def _check_quota() -> None:
    """No-op for local MCP profiles without HTTP billing."""
    return


'''
    text = re.sub(
        r"def _check_scopes\(\) -> None:.*?\n(?=def make_tool)",
        replacement,
        text,
        flags=re.DOTALL,
    )
    if text == original:
        return []
    path.write_text(text)
    return ["mcp_server/_tool_factory.py"]


def _pruned_path_groups(config: OnboardingConfig) -> dict[str, list[str]]:
    """Return prune groups implied by config."""
    groups: dict[str, list[str]] = {}

    base_rules = [
        (
            "mcp",
            ServiceSurface.MCP not in config.service_surfaces,
            [
                "mcp_server",
                "server.json",
                "smithery.yaml",
                ".mcp.json.example",
                ".github/workflows/mcp-registry-publish.yml",
            ],
        ),
        (
            "api",
            ServiceSurface.HTTP_API not in config.service_surfaces,
            ["api_server", "railway.json"],
        ),
        (
            "cli",
            ServiceSurface.CLI not in config.service_surfaces,
            ["src/cli", "tests/cli"],
        ),
        ("db", not config.database, ["db", "alembic.ini"]),
        (
            "docs",
            not config.docs,
            [
                "docs",
                ".github/workflows/docs-lint.yaml",
                ".github/workflows/jules-sync-translations.yml",
            ],
        ),
        (
            "auth",
            not config.auth,
            [
                "models/auth.py",
                "common/token_encryption.py",
                "tests/test_api_key_auth.py",
                "tests/test_unified_auth.py",
                "tests/test_workos_auth.py",
                "tests/test_scopes.py",
                "tests/test_token_encryption.py",
            ],
        ),
    ]
    for name, enabled, paths in base_rules:
        if enabled:
            groups[name] = paths

    if not config.auth and ServiceSurface.HTTP_API not in config.service_surfaces:
        groups.setdefault("auth", []).append("src/utils/current_user.py")

    _add_payment_prune_groups(config, groups)
    _add_example_prune_groups(config, groups)
    _add_surface_test_prune_groups(config, groups)
    return groups


def _add_payment_prune_groups(
    config: OnboardingConfig, groups: dict[str, list[str]]
) -> None:
    """Add payment-related prune path groups."""
    if not config.payments:
        groups["payments"] = [
            "src/payments",
            "common/payments.yaml",
            "common/subscription_config.yaml",
            "api_server/routes/agentic_payments.py",
            "api_server/routes/payments",
            "docs/agentic_payments_plan.md",
            "docs/agentic_payment_protocols_research.md",
            "tests/test_agentic_payments.py",
            "tests/test_agentic_payments_api.py",
            "tests/test_billing.py",
            "tests/test_payments_config.py",
        ]
    elif PaymentStack.X402 not in config.payments:
        groups["x402"] = [
            "src/payments/x402",
            "tests/test_agentic_payments.py",
            "tests/test_agentic_payments_api.py",
            "tests/test_payments_config.py",
        ]


def _add_example_prune_groups(
    config: OnboardingConfig, groups: dict[str, list[str]]
) -> None:
    """Add example-app prune path groups."""
    if ExampleApp.GMAIL_GOOGLE_OAUTH not in config.examples:
        groups["gmail_example"] = [
            "models/gmail.py",
            "services/gmail_svc.py",
            "services/gmail_messages_svc.py",
            "services/gmail_drafts_svc.py",
            "api_server/routes/google_oauth.py",
            "tests/test_gmail_services.py",
            "tests/test_gmail_composer_enhancer.py",
            "tests/test_gmail_inbox_enhancer.py",
            "tests/test_google_oauth.py",
        ]
        groups["gmail_example"].extend(
            [
                "mcp_server/apps/gmail_composer",
                "mcp_server/apps/gmail_inbox",
                "mcp_server/app_tools/gmail_composer.py",
                "mcp_server/app_tools/gmail_inbox.py",
                "mcp_server/enhancers/gmail_composer.py",
                "mcp_server/enhancers/gmail_inbox.py",
            ]
        )

    if ServiceSurface.MCP_APPS not in config.service_surfaces:
        groups.setdefault("mcp_apps", []).extend(
            [
                "mcp_server/apps",
                "mcp_server/app_tools/_auth_guard.py",
                "tests/test_enhancers.py",
            ]
        )


def _add_surface_test_prune_groups(
    config: OnboardingConfig, groups: dict[str, list[str]]
) -> None:
    """Add transport-specific test prune groups."""
    if ServiceSurface.HTTP_API not in config.service_surfaces:
        groups["api_tests"] = [
            "tests/test_api_server.py",
            "tests/test_error_handler.py",
            "tests/test_agentic_payments_api.py",
            "tests/test_rate_limit.py",
            "tests/test_health_enhanced.py",
            "tests/test_mcp_remote.py",
        ]

    if ServiceSurface.MCP not in config.service_surfaces:
        groups["mcp_tests"] = [
            "tests/test_mcp_server.py",
            "tests/test_mcp_remote.py",
            "tests/test_enhancers.py",
        ]

    if not config.database:
        groups["db_tests"] = ["tests/test_db_models.py"]


def apply_headless_onboarding(config: OnboardingConfig) -> list[str]:
    """Apply deterministic pruning for a resolved headless config."""
    changed: list[str] = []

    for group, paths in _pruned_path_groups(config).items():
        removed = _remove_paths(paths)
        changed.extend(f"{group}: {path}" for path in removed)

    changed.extend(_rewrite_pyproject_for_config(config))
    changed.extend(_rewrite_importlinter_for_config(config))
    changed.extend(_rewrite_api_server_for_config(config))
    changed.extend(_rewrite_mcp_server_for_config(config))

    make_targets: set[str] = set()
    if ServiceSurface.HTTP_API not in config.service_surfaces:
        make_targets.add("api")
    if ServiceSurface.CLI not in config.service_surfaces:
        make_targets.add("cli")
    if ServiceSurface.MCP not in config.service_surfaces:
        make_targets.update({"mcp", "mcp_inspect", "dev_host"})
    if ServiceSurface.MCP_APPS not in config.service_surfaces:
        make_targets.add("build_apps")
    if not config.docs:
        make_targets.update({"docs", "docs_lint"})
    if not config.database:
        make_targets.update({"db_migrate", "db_revision"})
    if make_targets and _remove_make_target_blocks(make_targets):
        changed.append("Makefile")

    if not config.payments:
        _remove_lines_matching(
            PROJECT_ROOT / ".env.example",
            ["STRIPE_", "X402_", "LANGFUSE_", "SUPABASE_"],
        )
        changed.append(".env.example")

    return changed


def print_headless_summary(config: OnboardingConfig, *, dry_run: bool) -> None:
    """Print the resolved headless config and action plan."""
    resolved = yaml.safe_dump(config.to_dict(), sort_keys=False).strip()
    table = Table(title="Headless Onboarding Plan")
    table.add_column("#", style="cyan", justify="right")
    table.add_column("Action", style="white")
    for index, action in enumerate(plan_onboarding(config), 1):
        table.add_row(str(index), action)

    rprint(
        Panel(
            f"[bold]Resolved config[/bold]\n{resolved}\n\n"
            f"[bold]Mode[/bold]\n{'Dry run' if dry_run else 'Apply'}",
            title="Onboarding Config",
            border_style="blue",
        )
    )
    console.print(table)


def run_headless_onboarding(config: OnboardingConfig, *, dry_run: bool) -> None:
    """Run the headless onboarding path.

    The first headless milestone resolves and validates project shape. The
    destructive pruning operations are intentionally centralized here and will be
    filled in profile-by-profile instead of scattered through the interactive
    prompts.
    """
    print_headless_summary(config, dry_run=dry_run)
    if dry_run:
        return
    changed = apply_headless_onboarding(config)
    if changed:
        rprint("\n[green]Applied onboarding changes:[/green]")
        for rel_path in changed:
            rprint(f"  {rel_path}")
    else:
        rprint("[yellow]No onboarding changes were needed.[/yellow]")


def _run_orchestrator() -> None:
    """Run the full onboarding flow, executing all steps in sequence."""
    project_name = _read_pyproject_name()
    rprint(
        Panel(
            f"[bold]{project_name}[/bold]\n\n"
            "This wizard will guide you through:\n"
            "  1. Branding - Pick emoji and colour scheme for the CLI\n"
            "  2. Rename - Set project name and description\n"
            "  3. CLI Name - Choose the CLI command name\n"
            "  4. Dependencies - Install project dependencies\n"
            "  5. Environment - Configure API keys and secrets\n"
            "  6. Hooks - Activate pre-commit hooks\n"
            "  7. MCP Server - Enable MCP server alongside CLI\n"
            "  8. Media - Generate banner and logo assets\n"
            "  9. Jules - Enable/disable automated maintenance workflows",
            title="Welcome to Project Onboarding",
            border_style="blue",
        )
    )

    total = len(STEPS)
    completed: list[str] = []
    skipped: list[str] = []

    for i, (label, cmd_name) in enumerate(STEPS, 1):
        rprint(f"\n[bold cyan]--- Step {i}/{total}: {label} ---[/bold cyan]")
        answer = questionary.select(
            "Run this step?",
            choices=["Yes", "Skip"],
            default="Yes",
        ).ask()
        if answer is None:
            raise typer.Abort()

        if answer == "Skip":
            skipped.append(label)
            rprint(f"[yellow]- {label} skipped[/yellow]")
            continue

        try:
            step_fn = STEP_FUNCTIONS[cmd_name]
            step_fn()  # ty: ignore[call-non-callable]
            completed.append(label)
        except (typer.Exit, SystemExit) as exc:
            code = getattr(exc, "code", getattr(exc, "exit_code", 1))
            if code != 0:
                rprint(f"[red]✗ {label} failed.[/red]")
                cont = questionary.confirm(
                    "Continue with remaining steps?", default=True
                ).ask()
                if cont is None or not cont:
                    raise typer.Abort() from None
                skipped.append(f"{label} (failed)")
            else:
                completed.append(label)

    _print_summary(completed, skipped)


def _print_summary(completed: list[str], skipped: list[str]) -> None:
    """Print the final onboarding summary."""
    lines: list[str] = []
    for name in completed:
        lines.append(f"[green]✓[/green] {name}")
    for name in skipped:
        lines.append(f"[yellow]-[/yellow] {name}")
    lines.append("")
    lines.append("[bold]Suggested next commands:[/bold]")
    lines.append("  make test    - Run tests")
    lines.append("  make ci      - Run CI checks")
    lines.append("  make all     - Run main application")

    rprint(Panel("\n".join(lines), title="Onboarding Summary", border_style="green"))


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    profile: Annotated[
        str | None,
        typer.Option(
            "--profile",
            help=(f"Headless preset: {_choice_values(OnboardingProfile)}."),
        ),
    ] = None,
    config: Annotated[
        Path | None,
        typer.Option("--config", help="YAML/JSON headless onboarding config."),
    ] = None,
    surfaces: Annotated[
        str | None,
        typer.Option(
            "--surfaces",
            help=f"Comma-separated service surfaces: {_choice_values(ServiceSurface)}.",
        ),
    ] = None,
    payments: Annotated[
        str | None,
        typer.Option(
            "--payments",
            help=f"Comma-separated payment stacks: {_choice_values(PaymentStack)}.",
        ),
    ] = None,
    examples: Annotated[
        str | None,
        typer.Option(
            "--examples",
            help=f"Comma-separated example apps/docs: {_choice_values(ExampleApp)}.",
        ),
    ] = None,
    auth: Annotated[
        bool | None,
        typer.Option("--auth/--no-auth", help="Keep or prune auth infrastructure."),
    ] = None,
    database: Annotated[
        bool | None,
        typer.Option(
            "--database/--no-database", help="Keep or prune DB infrastructure."
        ),
    ] = None,
    docs: Annotated[
        bool | None,
        typer.Option("--docs/--no-docs", help="Keep or prune docs site."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run", help="Print the headless plan without changing files."
        ),
    ] = False,
) -> None:
    """Run the full onboarding flow, a headless profile, or a specific step."""
    if ctx.invoked_subcommand is not None:
        return

    headless_requested = any(
        value is not None
        for value in (
            profile,
            config,
            surfaces,
            payments,
            examples,
            auth,
            database,
            docs,
        )
    )
    if not headless_requested:
        _run_orchestrator()
        return

    resolved = (
        load_onboarding_config(config)
        if config is not None
        else OnboardingConfig.for_profile(
            _coerce_choice(profile or OnboardingProfile.CUSTOM.value, OnboardingProfile)
        )
    )
    resolved = resolved.with_overrides(
        service_surfaces=(
            _coerce_choice_set(surfaces, ServiceSurface)
            if surfaces is not None
            else None
        ),
        payments=(
            _coerce_choice_set(payments, PaymentStack) if payments is not None else None
        ),
        examples=(
            _coerce_choice_set(examples, ExampleApp) if examples is not None else None
        ),
        auth=auth,
        database=database,
        docs=docs,
    )
    run_headless_onboarding(resolved, dry_run=dry_run)


def _save_cli_branding(emoji: str, primary_color: str, secondary_color: str) -> None:
    """Persist emoji and colour settings into common/global_config.yaml."""
    config_path = PROJECT_ROOT / "common" / "global_config.yaml"
    text = config_path.read_text()
    text = re.sub(r"^  emoji:.*$", f'  emoji: "{emoji}"', text, flags=re.MULTILINE)
    text = re.sub(
        r"^  primary_color:.*$",
        f'  primary_color: "{primary_color}"',
        text,
        flags=re.MULTILINE,
    )
    text = re.sub(
        r"^  secondary_color:.*$",
        f'  secondary_color: "{secondary_color}"',
        text,
        flags=re.MULTILINE,
    )
    config_path.write_text(text)


def _pick_emoji() -> str:
    """Prompt the user to pick or enter an emoji. Returns the chosen emoji."""
    rprint("\n[bold]Pick an emoji for your CLI:[/bold]")
    grid = "  ".join(PRESET_EMOJIS[:8]) + "\n  " + "  ".join(PRESET_EMOJIS[8:])
    rprint(f"  {grid}\n")

    emoji_choices = list(PRESET_EMOJIS) + ["✏️  Enter custom emoji"]
    selected = questionary.select("Select an emoji:", choices=emoji_choices).ask()
    if selected is None:
        raise typer.Abort()
    if selected == "✏️  Enter custom emoji":
        selected = questionary.text("Enter your emoji:").ask()
        if selected is None:
            raise typer.Abort()
    return selected


def _pick_color_scheme() -> tuple[str, str]:
    """Prompt the user to pick a colour scheme. Returns (primary_color, secondary_color)."""
    rprint("\n[bold]Pick a colour scheme:[/bold]")
    for name, primary, secondary, desc in COLOR_PALETTES:
        rprint(
            f"  [{primary}]■■■[/{primary}][{secondary}]■■■[/{secondary}]  "
            f"[bold]{name}[/bold] – {desc}"
        )
    rprint()

    palette_choices = [
        f"{name} – {desc}" for name, primary, secondary, desc in COLOR_PALETTES
    ]
    palette_choices += ["🎲 Auto-generate (random)", "✏️  Enter custom colours"]

    while True:
        selection = questionary.select(
            "Select a colour scheme:", choices=palette_choices
        ).ask()
        if selection is None:
            raise typer.Abort()

        if selection == "🎲 Auto-generate (random)":
            result = _try_random_scheme()
            if result is not None:
                return result
            continue  # Reroll or back to manual

        if selection == "✏️  Enter custom colours":
            return _enter_custom_colours()

        # Named palette selected
        for name, primary, secondary, desc in COLOR_PALETTES:
            if selection == f"{name} – {desc}":
                return primary, secondary

    return "cyan", "green"  # unreachable – satisfies type checker


def _try_random_scheme() -> tuple[str, str] | None:
    """Show a randomly generated scheme and return colours, or None to loop again."""
    name, primary, secondary, desc = random.choice(COLOR_PALETTES)
    rprint(
        f"\n  Generated: [bold]{name}[/bold] – {desc}\n"
        f"  [{primary}]■■■ {primary}[/{primary}]  "
        f"[{secondary}]■■■ {secondary}[/{secondary}]\n"
    )
    action = questionary.select(
        "What would you like to do?",
        choices=["✓ Use this scheme", "🎲 Reroll", "← Pick manually"],
        default="✓ Use this scheme",
    ).ask()
    if action is None:
        raise typer.Abort()
    if action == "✓ Use this scheme":
        return primary, secondary
    return None  # Reroll or pick manually → caller loops


def _enter_custom_colours() -> tuple[str, str]:
    """Prompt for custom Rich colour names and return (primary, secondary)."""
    rprint(
        "[dim]  Enter Rich colour names (e.g. cyan, bright_green) "
        "or hex (#ff0000)[/dim]"
    )
    primary = questionary.text("Primary colour:", default="cyan").ask() or "cyan"
    secondary = questionary.text("Secondary colour:", default="green").ask() or "green"
    return primary, secondary


@app.command()
def branding() -> None:
    """Step 1: Choose CLI emoji and colour scheme."""
    selected_emoji = _pick_emoji()
    primary_color, secondary_color = _pick_color_scheme()

    _save_cli_branding(selected_emoji, primary_color, secondary_color)

    rprint(
        Panel(
            f"Emoji:           {selected_emoji}\n"
            f"Primary colour:  [{primary_color}]{primary_color}[/{primary_color}]\n"
            f"Secondary colour:[{secondary_color}]{secondary_color}[/{secondary_color}]",
            title="✅ Branding Complete",
            border_style="green",
        )
    )


_RENAME_EXTENSIONS = {
    ".py",
    ".toml",
    ".md",
    ".mdx",
    ".yml",
    ".yaml",
    ".json",
    ".tsx",
    ".ts",
    ".sh",
    ".txt",
}
_RENAME_SKIP_DIRS = {
    ".venv",
    ".venv-test",
    ".git",
    "node_modules",
    "__pycache__",
    ".uv_cache",
}
_RENAME_SKIP_FILES = {"uv.lock", "onboard.py", "install-skills.sh"}


def _should_process(path: Path) -> bool:
    """Check if a file should be included in bulk replacement."""
    if not path.is_file() or path.suffix not in _RENAME_EXTENSIONS:
        return False
    if path.name in _RENAME_SKIP_FILES:
        return False
    return not any(part in _RENAME_SKIP_DIRS for part in path.parts)


def _apply_replacements(text: str, replacements: list[tuple[str, str]]) -> str:
    """Apply all replacement pairs to a string."""
    for old, new in replacements:
        text = text.replace(old, new)
    return text


def _replace_in_files(replacements: list[tuple[str, str]]) -> list[str]:
    """Replace old->new pairs across all matching files in the project.

    Skips .venv, .git, node_modules, __pycache__, and uv.lock.
    Returns a list of relative paths that were modified.
    """
    changed: list[str] = []
    for path in sorted(PROJECT_ROOT.rglob("*")):
        if not _should_process(path):
            continue
        try:
            text = path.read_text()
        except (UnicodeDecodeError, PermissionError):
            continue
        new_text = _apply_replacements(text, replacements)
        if new_text != text:
            path.write_text(new_text)
            changed.append(str(path.relative_to(PROJECT_ROOT)))
    return changed


#: Template values that get replaced during onboarding rename
_TEMPLATE_PACKAGE_NAME = "miyamura80-cli-template"
_TEMPLATE_OWNER = "Miyamura80"
_TEMPLATE_REPO_NAMES = ["MCP-Template", "CLI-Template"]


def _build_rename_replacements(
    name: str,
    description: str,
    github_owner: str,
    github_repo: str,
) -> list[tuple[str, str]]:
    """Build replacement pairs for the rename step (order matters, most specific first)."""
    pairs: list[tuple[str, str]] = []

    # Package name (PyPI) - broader match first to avoid double-substitution
    pairs.append((_TEMPLATE_PACKAGE_NAME, name))
    if "python-template" not in name:
        pairs.append(("python-template", name))

    # GitHub owner/repo URLs (handle both old and current repo names)
    for old_repo in _TEMPLATE_REPO_NAMES:
        pairs.append((f"{_TEMPLATE_OWNER}/{old_repo}", f"{github_owner}/{github_repo}"))
        # URL-encoded form (used in badge URLs)
        pairs.append(
            (
                f"{_TEMPLATE_OWNER}%2F{old_repo}",
                f"{github_owner}%2F{github_repo}",
            )
        )

    # Standalone owner references (CODEOWNERS, author)
    pairs.append((f"@{_TEMPLATE_OWNER}", f"@{github_owner}"))
    pairs.append((f'name = "{_TEMPLATE_OWNER}"', f'name = "{github_owner}"'))

    if description:
        safe_description = description.replace('"', '\\"')
        pairs.append(("Add your description here", safe_description))

    pairs.append(("# cli-template", f"# {name}"))
    return pairs


def _prompt_github_info() -> tuple[str, str]:
    """Prompt for GitHub owner and repo, auto-detecting from git remote."""
    github_owner, github_repo = _read_github_owner_repo()

    def _nonempty(v: str) -> bool | str:
        return True if v.strip() else "Cannot be empty."

    if github_owner in ("OWNER", _TEMPLATE_OWNER):
        entered = questionary.text(
            "GitHub owner/org (e.g. my-github-username):",
            validate=_nonempty,
        ).ask()
        if entered is None:
            raise typer.Abort()
        github_owner = entered.strip()

    if github_repo in ("REPO", *_TEMPLATE_REPO_NAMES):
        entered = questionary.text(
            "GitHub repository name:",
            validate=_nonempty,
        ).ask()
        if entered is None:
            raise typer.Abort()
        github_repo = entered.strip()

    return github_owner, github_repo


@app.command()
def rename() -> None:
    """Step 2: Rename the project and update metadata."""
    current_name = _read_pyproject_name()
    if current_name not in ("python-template", _TEMPLATE_PACKAGE_NAME):
        rprint(
            f"[blue]ℹ Project already renamed to '{current_name}'. Skipping rename step.[/blue]"
        )
        return

    name = questionary.text(
        "Project name (kebab-case):",
        validate=_validate_kebab_case,
    ).ask()
    if name is None:
        raise typer.Abort()

    description = questionary.text("Project description:").ask()
    if description is None:
        raise typer.Abort()

    github_owner, github_repo = _prompt_github_info()
    replacements = _build_rename_replacements(
        name, description, github_owner, github_repo
    )
    changed_files = _replace_in_files(replacements)

    changes = [f"  [green]{f}[/green]" for f in changed_files]
    summary_lines = [
        f"Package name: [green]{name}[/green]",
        f"GitHub:       [green]{github_owner}/{github_repo}[/green]",
    ]
    if description:
        summary_lines.append(f"Description:  [green]{description}[/green]")
    summary_lines.append("")
    summary_lines.append(f"Updated [bold]{len(changed_files)}[/bold] file(s):")
    summary_lines.extend(changes)

    rprint(
        Panel(
            "\n".join(summary_lines), title="✅ Rename Complete", border_style="green"
        )
    )


def _replace_cli_name(old_name: str, new_name: str) -> list[str]:
    """Replace all occurrences of the old CLI name with the new one across the codebase.

    Returns a list of human-readable change descriptions.
    """
    old_upper = old_name.upper().replace("-", "_")
    new_upper = new_name.upper().replace("-", "_")

    # Map of (file_path, [(old, new), ...])
    replacements: list[tuple[Path, list[tuple[str, str]]]] = [
        (
            PROJECT_ROOT / "pyproject.toml",
            [
                (
                    f'{old_name} = "src.cli.app:main_cli"',
                    f'{new_name} = "src.cli.app:main_cli"',
                ),
                (
                    f'{old_name}-mcp = "mcp_server:main"',
                    f'{new_name}-mcp = "mcp_server:main"',
                ),
            ],
        ),
        (
            PROJECT_ROOT / "src" / "cli" / "app.py",
            [
                (f'name="{old_name}"', f'name="{new_name}"'),
                (f"{old_name} {{version}}", f"{new_name} {{version}}"),
            ],
        ),
        (
            PROJECT_ROOT / "src" / "cli" / "completions.py",
            [
                (f'"_{old_upper}_COMPLETE"', f'"_{new_upper}_COMPLETE"'),
                (f'which("{old_name}")', f'which("{new_name}")'),
                (f"completions for {old_name}.", f"completions for {new_name}."),
                (
                    f"[bold]{old_name} --install-completion[/bold]",
                    f"[bold]{new_name} --install-completion[/bold]",
                ),
                (
                    f"[bold]{old_name} --show-completion[/bold]",
                    f"[bold]{new_name} --show-completion[/bold]",
                ),
                (f"# {old_name} completions", f"# {new_name} completions"),
            ],
        ),
        (
            PROJECT_ROOT / "src" / "cli" / "telemetry.py",
            [(f"'{old_name} telemetry disable'", f"'{new_name} telemetry disable'")],
        ),
        (
            PROJECT_ROOT / "src" / "cli" / "scaffold.py",
            [(f"[bold]{old_name} ", f"[bold]{new_name} ")],
        ),
        (
            PROJECT_ROOT / "tests" / "cli" / "test_cli.py",
            [(f'"{old_name}"', f'"{new_name}"')],
        ),
        (
            PROJECT_ROOT / ".mcp.json.example",
            [
                (f'"{old_name}"', f'"{new_name}"'),
                (f"{old_name}-mcp", f"{new_name}-mcp"),
            ],
        ),
        (
            PROJECT_ROOT / "smithery.yaml",
            [(f"{old_name}-mcp", f"{new_name}-mcp")],
        ),
        (
            PROJECT_ROOT / "mcp_server" / "server.py",
            [(f'FastMCP("{old_name}")', f'FastMCP("{new_name}")')],
        ),
        (
            PROJECT_ROOT / "Makefile",
            [(f"{old_name}-mcp", f"{new_name}-mcp")],
        ),
    ]

    # Files where we use regex word-boundary replacement instead of literal
    regex_replacements: list[tuple[Path, str, str]] = [
        (PROJECT_ROOT / "README.md", rf"\b{re.escape(old_name)}\b", new_name),
        (
            PROJECT_ROOT / ".agents" / "skills" / "push-release" / "SKILL.md",
            rf"\b{re.escape(old_name)}\b",
            new_name,
        ),
        (
            PROJECT_ROOT / ".claude" / "skills" / "usage" / "SKILL.md",
            rf"\b{re.escape(old_name)}\b",
            new_name,
        ),
    ]

    changes: list[str] = []
    for file_path, pairs in replacements:
        if not file_path.exists():
            continue
        text = file_path.read_text()
        file_changed = False
        for old, new in pairs:
            if old in text:
                text = text.replace(old, new)
                file_changed = True
        if file_changed:
            file_path.write_text(text)
            rel = file_path.relative_to(PROJECT_ROOT)
            changes.append(f"[green]{rel}[/green]")

    for file_path, pattern, repl in regex_replacements:
        if not file_path.exists():
            continue
        text = file_path.read_text()
        new_text = re.sub(pattern, repl, text)
        if new_text != text:
            file_path.write_text(new_text)
            rel = file_path.relative_to(PROJECT_ROOT)
            if f"[green]{rel}[/green]" not in changes:
                changes.append(f"[green]{rel}[/green]")

    return changes


@app.command()
def cli_name() -> None:
    """Step 3: Choose the CLI command name (renames all 'mymcp' references)."""
    current = _read_cli_name()
    if current != "mymcp":
        rprint(
            f"[blue]ℹ CLI already renamed to '{current}'. Skipping CLI name step.[/blue]"
        )
        return

    name = questionary.text(
        "CLI command name (e.g. my-tool):",
        default="mymcp",
        validate=_validate_cli_name,
    ).ask()
    if name is None:
        raise typer.Abort()

    if name == "mymcp":
        rprint("[yellow]Keeping default name 'mymcp'.[/yellow]")
        return

    changed_files = _replace_cli_name("mymcp", name)

    if not changed_files:
        rprint("[yellow]No files needed updating.[/yellow]")
        return

    rprint(
        Panel(
            f"Renamed CLI from [red]mymcp[/red] → [green]{name}[/green]\n\n"
            "Updated files:\n" + "\n".join(f"  {f}" for f in changed_files),
            title="✅ CLI Name Complete",
            border_style="green",
        )
    )


@app.command()
def deps() -> None:
    """Step 4: Install project dependencies."""
    if not shutil.which("uv"):
        rprint(
            "[red]✗ uv is not installed.[/red]\n"
            "  Install it from: [link=https://docs.astral.sh/uv]https://docs.astral.sh/uv[/link]"
        )
        raise typer.Exit(code=1)

    venv_path = PROJECT_ROOT / ".venv"
    if not venv_path.is_dir():
        with console.status("[yellow]Creating virtual environment...[/yellow]"):
            result = subprocess.run(
                ["uv", "venv"],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                rprint(f"[red]✗ Failed to create venv:[/red]\n{result.stderr}")
                raise typer.Exit(code=1)
        rprint("[green]✓[/green] Virtual environment created.")

    with console.status("[yellow]Installing dependencies (uv sync)...[/yellow]"):
        result = subprocess.run(
            ["uv", "sync"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )
    if result.returncode != 0:
        rprint(f"[red]✗ uv sync failed:[/red]\n{result.stderr}")
        raise typer.Exit(code=1)

    rprint("[green]✓ Dependencies installed successfully.[/green]")


def _is_secret_key(name: str) -> bool:
    """Check if an env var name suggests a secret value."""
    return any(word in name.upper() for word in ("SECRET", "KEY", "TOKEN", "PASSWORD"))


def _parse_env_example() -> list[dict[str, str]]:
    """Parse .env.example into a list of entries with group, key, and default value.

    Returns a list of dicts with keys: 'group', 'key', 'default'.
    Comment-only lines set the current group. Blank lines are skipped.
    """
    env_example_path = PROJECT_ROOT / ".env.example"
    if not env_example_path.exists():
        return []

    entries: list[dict[str, str]] = []
    current_group = "General"

    for line in env_example_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            current_group = line.lstrip("# ").strip()
            continue
        if "=" in line:
            key, _, default = line.partition("=")
            entries.append(
                {"group": current_group, "key": key.strip(), "default": default.strip()}
            )

    return entries


def _load_existing_env() -> dict[str, str]:
    """Load existing .env file into a dict."""
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return {}

    result: dict[str, str] = {}
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            result[key.strip()] = value.strip()
    return result


def _has_real_value(value: str) -> bool:
    """Check if an env var value is a real (non-placeholder) value."""
    if not value:
        return False
    placeholders = {
        "sk-...",
        "sk-ant-...",
        "xai-...",
        "gsk_...",
        "pplx-...",
        "AIza...",
        "csk-...",
        "sk-lf-...",
        "pk-lf-...",
        "sk_test_...",
        "ghp_...",
        "postgresql://user:pass@host:port/db",
        "https://your-project.supabase.co",
    }
    return value not in placeholders


def _build_env_choices(
    entries: list[dict[str, str]], existing: dict[str, str]
) -> list[questionary.Choice]:
    """Build questionary checkbox choices from env entries."""
    choices = []
    for entry in entries:
        key = entry["key"]
        has_value = _has_real_value(existing.get(key, ""))
        label = f"[{entry['group']}] {key}"
        if has_value:
            label += " (configured)"
        choices.append(questionary.Choice(title=label, value=key, checked=has_value))
    return choices


def _prompt_env_value(key: str, default: str, current_value: str) -> str:
    """Prompt the user for a single env var value, handling existing values."""
    if _has_real_value(current_value):
        keep = questionary.confirm(
            f"{key} already has a value. Keep existing value?",
            default=True,
        ).ask()
        if keep is None:
            raise typer.Abort()
        if keep:
            return current_value

    prompt_fn = questionary.password if _is_secret_key(key) else questionary.text
    default_hint = default if not _is_secret_key(key) else ""
    new_value = prompt_fn(f"{key}:", default=default_hint).ask()
    if new_value is None:
        raise typer.Abort()
    return new_value


def _write_env_file(entries: list[dict[str, str]], values: dict[str, str]) -> int:
    """Write .env file preserving group structure and custom vars. Returns count of skipped keys."""
    # Load existing env and identify custom variables not in .env.example
    existing = _load_existing_env()
    tracked_keys = {entry["key"] for entry in entries}
    custom_vars = {k: v for k, v in existing.items() if k not in tracked_keys}

    lines: list[str] = []
    current_group = ""
    skipped = 0

    for entry in entries:
        if entry["group"] != current_group:
            if lines:
                lines.append("")
            lines.append(f"# {entry['group']}")
            current_group = entry["group"]

        key = entry["key"]
        if key in values:
            lines.append(f"{key}={values[key]}")
        else:
            lines.append(f"# {key}={entry['default']}")
            skipped += 1

    # Preserve custom variables not in .env.example
    if custom_vars:
        lines.append("")
        lines.append("# Custom variables")
        for key, value in custom_vars.items():
            lines.append(f"{key}={value}")

    (PROJECT_ROOT / ".env").write_text("\n".join(lines) + "\n")
    return skipped


@app.command()
def env() -> None:
    """Step 5: Configure environment variables."""
    entries = _parse_env_example()
    if not entries:
        rprint("[red]✗ No .env.example found.[/red]")
        raise typer.Exit(code=1)

    existing = _load_existing_env()
    choices = _build_env_choices(entries, existing)

    selected_keys = questionary.checkbox(
        "Select environment variables to configure:",
        choices=choices,
    ).ask()
    if selected_keys is None:
        raise typer.Abort()

    selected_set = set(selected_keys)
    values: dict[str, str] = {}
    for entry in entries:
        key = entry["key"]
        if key not in selected_set:
            continue
        values[key] = _prompt_env_value(key, entry["default"], existing.get(key, ""))

    skipped = _write_env_file(entries, values)
    configured = len(values)

    rprint(
        f"\n[green]✓ {configured} key(s) configured, {skipped} key(s) skipped.[/green]"
    )


def _ensure_prek() -> None:
    """Prompt to install prek if not found on PATH."""
    if shutil.which("prek"):
        return
    rprint("[yellow]⚠ prek is not installed.[/yellow]")
    install = questionary.confirm(
        "Install prek via 'uv tool install prek'?",
        default=True,
    ).ask()
    if install is None:
        raise typer.Abort()
    if not install:
        rprint("[red]✗ prek is required for pre-commit hooks.[/red]")
        raise typer.Exit(code=1)
    result = subprocess.run(
        ["uv", "tool", "install", "prek"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        rprint(f"[red]✗ Failed to install prek:[/red]\n{result.stderr}")
        raise typer.Exit(code=1)
    rprint("[green]✓ prek installed.[/green]")


@app.command()
def hooks() -> None:
    """Step 6: Activate pre-commit hooks."""
    config_path = PROJECT_ROOT / "prek.toml"
    if not config_path.exists():
        rprint("[red]✗ prek.toml not found.[/red]")
        raise typer.Exit(code=1)

    _ensure_prek()

    config = tomllib.loads(config_path.read_text())

    table = Table(title="Configured Pre-commit Hooks (prek)")
    table.add_column("Hook ID", style="cyan")
    table.add_column("Description", style="white")

    for repo in config.get("repos", []):
        for hook in repo.get("hooks", []):
            hook_id = hook.get("id", "unknown")
            hook_name = hook.get("name", hook_id)
            table.add_row(hook_id, hook_name)

    console.print(table)
    rprint("")

    activate = questionary.confirm(
        "Activate pre-commit hooks? (Recommended)",
        default=True,
    ).ask()
    if activate is None:
        raise typer.Abort()

    if activate:
        result = subprocess.run(
            ["prek", "install"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            rprint(f"[red]✗ Failed to activate hooks:[/red]\n{result.stderr}")
            raise typer.Exit(code=1)
        rprint("[green]✓ Pre-commit hooks activated (prek).[/green]")
    else:
        rprint(
            "[yellow]Skipped.[/yellow] You can activate later with: "
            "[bold]prek install[/bold]"
        )


def _check_gemini_key() -> bool:
    """Check if GEMINI_API_KEY is available in .env or environment."""
    if os.environ.get("GEMINI_API_KEY"):
        return True
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line.startswith("GEMINI_API_KEY=") and not line.startswith("#"):
                value = line.split("=", 1)[1].strip()
                return _has_real_value(value)
    return False


def _run_media_generation(choice: str, project_name: str, theme: str) -> list[str]:
    """Run the selected media generation and return list of generated file paths."""
    # Import here to avoid requiring GEMINI_API_KEY for non-media commands
    from init.generate_banner import generate_banner as gen_banner  # noqa: PLC0415
    from init.generate_logo import generate_logo as gen_logo  # noqa: PLC0415

    generated_files: list[str] = []

    if choice in ("Banner only", "Both"):
        with console.status("[yellow]Generating banner...[/yellow]"):
            asyncio.run(gen_banner(title=project_name, theme=theme))
        banner_path = PROJECT_ROOT / "media" / "banner.png"
        generated_files.append(str(banner_path))
        rprint(f"[green]✓[/green] Banner saved to {banner_path}")

    if choice in ("Logo only", "Both"):
        with console.status("[yellow]Generating logo...[/yellow]"):
            asyncio.run(gen_logo(project_name=project_name, theme=theme))
        logo_dir = PROJECT_ROOT / "docs" / "public"
        for name in (
            "logo-light.png",
            "logo-dark.png",
            "icon-light.png",
            "icon-dark.png",
            "favicon.ico",
        ):
            generated_files.append(str(logo_dir / name))
        rprint(f"[green]✓[/green] Logo assets saved to {logo_dir}")

    return generated_files


@app.command()
def media() -> None:
    """Step 8: Generate banner and logo assets."""
    if not _check_gemini_key():
        rprint("[yellow]⚠ GEMINI_API_KEY is not configured.[/yellow]")
        skip = questionary.confirm("Skip media generation?", default=True).ask()
        if skip is None:
            raise typer.Abort()
        if skip:
            rprint("[yellow]Media generation skipped.[/yellow]")
            return

    project_name = _read_pyproject_name()

    rprint()
    theme = questionary.text(
        "Describe the visual theme/style for your project assets:",
        default="modern, clean, minimalist tech aesthetic",
    ).ask()
    if theme is None:
        raise typer.Abort()

    choice = questionary.select(
        "What would you like to generate?",
        choices=["Both", "Banner only", "Logo only", "Skip"],
        default="Both",
    ).ask()
    if choice is None:
        raise typer.Abort()

    if choice == "Skip":
        rprint("[yellow]Media generation skipped.[/yellow]")
        return

    generated_files = _run_media_generation(choice, project_name, theme)
    rprint("\n[green]Generated files:[/green]")
    for f in generated_files:
        rprint(f"  {f}")


@app.command()
def mcp() -> None:
    """Step 7: Enable MCP server alongside CLI."""
    enable = questionary.confirm("Enable MCP server alongside CLI?", default=True).ask()
    if enable is None:
        raise typer.Abort()

    if not enable:
        _disable_mcp()
        rprint("[yellow]MCP server disabled. Removed MCP-related files.[/yellow]")
        return

    cli_name = _read_cli_name()
    default_mcp_name = f"{cli_name}-mcp"
    mcp_name = questionary.text("MCP entrypoint name:", default=default_mcp_name).ask()
    if mcp_name is None:
        raise typer.Abort()

    if mcp_name != default_mcp_name:
        _update_mcp_entrypoint(default_mcp_name, mcp_name)

    _update_mcp_distribution_files(cli_name, mcp_name)

    rprint(
        Panel(
            f"MCP entrypoint: [green]{mcp_name}[/green]\n"
            f"Run locally:    [cyan]uv run {mcp_name}[/cyan]\n"
            f"Inspector:      [cyan]uv run mcp dev mcp_server/server.py[/cyan]",
            title="MCP Server Enabled",
            border_style="green",
        )
    )


def _disable_mcp() -> None:
    """Remove MCP-related files and config when user opts out."""
    for path in (
        PROJECT_ROOT / "mcp_server",
        PROJECT_ROOT / "server.json",
        PROJECT_ROOT / "smithery.yaml",
        PROJECT_ROOT / ".mcp.json.example",
        PROJECT_ROOT / ".github" / "workflows" / "mcp-registry-publish.yml",
    ):
        if path.is_dir():
            shutil.rmtree(path)
        elif path.is_file():
            path.unlink()

    pyproject_path = PROJECT_ROOT / "pyproject.toml"
    text = pyproject_path.read_text()
    # Remove mymcp-mcp entrypoint line
    text = re.sub(r'^.*-mcp\s*=\s*"mcp_server:main"\s*\n', "", text, flags=re.MULTILINE)
    # Remove mcp dependency line
    text = re.sub(r'^\s*"mcp\[cli\].*",?\s*\n', "", text, flags=re.MULTILINE)
    # Remove mcp_server from hatch packages list and vulture exclude paths
    text = re.sub(r',?\s*"mcp_server/?"', "", text)
    pyproject_path.write_text(text)

    # Clean up .importlinter references to mcp_server
    importlinter_path = PROJECT_ROOT / ".importlinter"
    if importlinter_path.exists():
        il_text = importlinter_path.read_text()
        # Remove mcp_server from root_packages and contract module lists
        il_text = re.sub(r"^\s*mcp_server\s*\n", "", il_text, flags=re.MULTILINE)
        # Remove the services_no_transport contract's mcp_server forbidden entry
        # (already handled by the line removal above)
        importlinter_path.write_text(il_text)


def _update_mcp_entrypoint(old_name: str, new_name: str) -> None:
    """Update the MCP entrypoint name in pyproject.toml and distribution files."""
    pyproject_path = PROJECT_ROOT / "pyproject.toml"
    text = pyproject_path.read_text()
    text = text.replace(
        f'{old_name} = "mcp_server:main"', f'{new_name} = "mcp_server:main"'
    )
    pyproject_path.write_text(text)


def _read_github_owner_repo() -> tuple[str, str]:
    """Extract owner and repo from the git remote URL. Falls back to placeholders."""
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=5,
        )
        url = result.stdout.strip()
        # Match github.com/OWNER/REPO from HTTPS or SSH URLs
        match = re.search(r"github\.com[:/]([^/]+)/([^/.]+?)(?:\.git)?$", url)
        if match:
            return match.group(1), match.group(2)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return "OWNER", "REPO"


def _update_mcp_distribution_files(cli_name: str, mcp_name: str) -> None:
    """Fill in placeholders in server.json, smithery.yaml, and .mcp.json.example."""
    project_name = _read_pyproject_name()
    github_owner, github_repo = _read_github_owner_repo()

    # Read version and description from pyproject.toml
    pyproject_text = (PROJECT_ROOT / "pyproject.toml").read_text()
    version_match = re.search(r'^version\s*=\s*"([^"]*)"', pyproject_text, re.MULTILINE)
    version = version_match.group(1) if version_match else "0.1.0"
    desc_match = re.search(
        r'^description\s*=\s*"([^"]*)"', pyproject_text, re.MULTILINE
    )
    description = desc_match.group(1) if desc_match else project_name

    # Update .mcp.json.example
    mcp_json_path = PROJECT_ROOT / ".mcp.json.example"
    if mcp_json_path.exists():
        text = mcp_json_path.read_text()
        text = text.replace("mymcp-mcp", mcp_name)
        text = text.replace('"mymcp"', f'"{cli_name}"')
        mcp_json_path.write_text(text)

    # Update server.json
    server_json_path = PROJECT_ROOT / "server.json"
    if server_json_path.exists():
        text = server_json_path.read_text()
        text = text.replace("OWNER/REPO", f"{github_owner}/{github_repo}")
        text = text.replace("DESCRIPTION", description)
        text = text.replace("PYPI_PACKAGE_NAME", project_name)
        text = re.sub(r'"version":\s*"0\.1\.0"', f'"version": "{version}"', text)
        server_json_path.write_text(text)

    # Update smithery.yaml
    smithery_path = PROJECT_ROOT / "smithery.yaml"
    if smithery_path.exists():
        text = smithery_path.read_text()
        text = text.replace("mymcp-mcp", mcp_name)
        smithery_path.write_text(text)


_JULES_WORKFLOWS: list[tuple[str, str]] = [
    (
        "jules-prune-unnecessary-code.yml",
        "Dead code cleanup (Wednesdays 2pm UTC)",
    ),
]

_WORKFLOWS_DIR = PROJECT_ROOT / ".github" / "workflows"


def _workflow_enabled(filename: str) -> bool:
    """Check if a Jules workflow file is enabled (not disabled)."""
    return (_WORKFLOWS_DIR / filename).exists() and not (
        _WORKFLOWS_DIR / f"{filename}.disabled"
    ).exists()


def _enable_workflow(filename: str) -> None:
    """Enable a workflow by renaming .disabled back to .yml."""
    disabled = _WORKFLOWS_DIR / f"{filename}.disabled"
    enabled = _WORKFLOWS_DIR / filename
    if disabled.exists() and not enabled.exists():
        disabled.rename(enabled)


def _disable_workflow(filename: str) -> None:
    """Disable a workflow by renaming .yml to .yml.disabled."""
    enabled = _WORKFLOWS_DIR / filename
    if enabled.exists():
        enabled.rename(_WORKFLOWS_DIR / f"{filename}.disabled")


@app.command()
def jules() -> None:
    """Step 9: Enable or disable automated Jules maintenance workflows."""
    if not _WORKFLOWS_DIR.is_dir():
        rprint("[red]✗ .github/workflows/ directory not found.[/red]")
        raise typer.Exit(code=1)

    table = Table(title="Jules Maintenance Workflows")
    table.add_column("Workflow", style="cyan")
    table.add_column("Schedule", style="white")
    table.add_column("Status", style="white")

    for filename, description in _JULES_WORKFLOWS:
        enabled = _workflow_enabled(filename)
        status = "[green]enabled[/green]" if enabled else "[yellow]disabled[/yellow]"
        table.add_row(filename, description, status)

    console.print(table)
    rprint("")

    choices = []
    for filename, description in _JULES_WORKFLOWS:
        enabled = _workflow_enabled(filename)
        label = f"{description}"
        if enabled:
            label += " (enabled)"
        choices.append(questionary.Choice(title=label, value=filename, checked=enabled))

    selected = questionary.checkbox(
        "Select which Jules workflows to enable:",
        choices=choices,
    ).ask()
    if selected is None:
        raise typer.Abort()

    selected_set = set(selected)
    changes: list[str] = []

    for filename, description in _JULES_WORKFLOWS:
        was_enabled = _workflow_enabled(filename)
        should_enable = filename in selected_set

        if should_enable and not was_enabled:
            _enable_workflow(filename)
            changes.append(f"[green]✓[/green] Enabled {description}")
        elif not should_enable and was_enabled:
            _disable_workflow(filename)
            changes.append(f"[yellow]-[/yellow] Disabled {description}")
        elif should_enable:
            changes.append(f"[blue]·[/blue] {description} (already enabled)")
        else:
            changes.append(f"[blue]·[/blue] {description} (already disabled)")

    rprint(
        Panel(
            "\n".join(changes)
            + "\n\n[dim]Note: JULES_API_KEY secret must be configured in "
            "repository Actions settings.[/dim]",
            title="Jules Workflows",
            border_style="green",
        )
    )


# Register step functions for the orchestrator
STEP_FUNCTIONS.update(
    {
        "branding": branding,
        "rename": rename,
        "cli_name": cli_name,
        "deps": deps,
        "env": env,
        "hooks": hooks,
        "mcp": mcp,
        "media": media,
        "jules": jules,
    }
)

if __name__ == "__main__":
    app()
