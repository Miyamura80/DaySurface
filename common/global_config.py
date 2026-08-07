import os
import re
import warnings
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values, load_dotenv
from loguru import logger
from pydantic import Field, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

# Import configuration models
from .config_models import (
    AgenticPaymentsConfig,
    AskConfig,
    BrandingConfig,
    CliConfig,
    DefaultLlm,
    ExampleParent,
    FeaturesConfig,
    GmailConfig,
    LlmConfig,
    LoggingConfig,
    PdfFormsConfig,
    RateLimitConfig,
    ServerConfig,
    SubscriptionConfig,
    TelemetryConfig,
    WebBotAuthConfig,
)

# Get the path to the root directory (one level up from common)
root_dir = Path(__file__).parent.parent

OPENAI_O_SERIES_PATTERN = r"o(\d+)(-mini)?"

# Merged YAML view (env-free), captured once when the Config singleton is built
# (see Config.settings_customise_sources). Consumed by Config.to_yaml_dict() as
# the secret-free config view for the config_show / config_get services.
_yaml_config_cache: dict[str, Any] = {}


# Custom YAML settings source
class YamlSettingsSource(PydanticBaseSettingsSource):
    """
    Custom settings source that loads from YAML files with priority:
    1. .global_config.yaml (highest priority, git-ignored)
    2. production_config.yaml (if DEV_ENV=prod)
    3. global_config.yaml (base config)
    """

    def __init__(self, settings_cls: type[BaseSettings]):
        super().__init__(settings_cls)
        self.yaml_data = self._load_yaml_files()

    def _load_yaml_files(self) -> dict[str, Any]:  # noqa: C901
        """Load and merge YAML configuration files."""

        def recursive_update(default: dict, override: dict) -> dict:
            """Recursively update nested dictionaries."""
            for key, value in override.items():
                if isinstance(value, dict) and isinstance(default.get(key), dict):
                    recursive_update(default[key], value)
                else:
                    default[key] = value
            return default

        # Load base config
        config_path = root_dir / "common" / "global_config.yaml"
        try:
            with open(config_path, "r") as file:
                config_data = yaml.safe_load(file) or {}
        except FileNotFoundError as e:
            raise RuntimeError(f"Required config file not found: {config_path}") from e
        except yaml.YAMLError as e:
            raise RuntimeError(f"Invalid YAML in {config_path}: {e}") from e

        # Load split YAML files from common/ directory
        reserved_filenames = {
            "global_config.yaml",
            "production_config.yaml",
            ".global_config.yaml",
        }
        common_dir = root_dir / "common"
        split_files = sorted(common_dir.glob("*.yaml"))
        for split_file in split_files:
            if split_file.name in reserved_filenames:
                continue
            # Security: skip symlinks to prevent loading files outside common/
            if split_file.is_symlink():
                logger.warning(f"Skipping symlink config file: {split_file}")
                continue
            root_key = split_file.stem
            if root_key in config_data:
                raise KeyError(
                    f"Config conflict: key '{root_key}' from '{split_file.name}' "
                    f"already exists in global_config.yaml. Remove it from one location."
                )
            try:
                with open(split_file, "r") as file:
                    split_data = yaml.safe_load(file)
                if split_data is not None:
                    config_data[root_key] = split_data
                    logger.debug(
                        f"Loaded split config: {split_file.name} -> '{root_key}'"
                    )
            except yaml.YAMLError as e:
                raise RuntimeError(f"Invalid YAML in {split_file}: {e}") from e

        # Load production config if in prod environment
        if os.getenv("DEV_ENV") == "prod":
            prod_config_path = root_dir / "common" / "production_config.yaml"
            if prod_config_path.exists():
                try:
                    with open(prod_config_path, "r") as file:
                        prod_config_data = yaml.safe_load(file)
                    if prod_config_data:
                        config_data = recursive_update(config_data, prod_config_data)
                        logger.warning(
                            "\033[33m❗️ Overwriting common/global_config.yaml with common/production_config.yaml\033[0m"
                        )
                except FileNotFoundError:
                    logger.warning(
                        f"Production config file not found: {prod_config_path}"
                    )
                except yaml.YAMLError as e:
                    raise RuntimeError(
                        f"Invalid YAML in {prod_config_path}: {e}"
                    ) from e

        # Load custom local config if it exists (highest priority)
        custom_config_path = root_dir / ".global_config.yaml"
        if custom_config_path.exists():
            try:
                with open(custom_config_path, "r") as file:
                    custom_config_data = yaml.safe_load(file)

                if custom_config_data:
                    config_data = recursive_update(config_data, custom_config_data)
                    warning_msg = "\033[33m❗️ Overwriting default common/global_config.yaml with .global_config.yaml\033[0m"
                    if config_data.get("logging", {}).get("verbose"):
                        warning_msg += f"\033[33mCustom .global_config.yaml values:\n---\n{yaml.dump(custom_config_data, default_flow_style=False)}\033[0m"
                    logger.warning(warning_msg)
            except FileNotFoundError:
                logger.warning(f"Custom config file not found: {custom_config_path}")
            except yaml.YAMLError as e:
                raise RuntimeError(f"Invalid YAML in {custom_config_path}: {e}") from e

        return config_data

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        """Get field value from YAML data."""
        field_value = self.yaml_data.get(field_name)
        return field_value, field_name, False

    def __call__(self) -> dict[str, Any]:
        """Return the complete YAML configuration."""
        return self.yaml_data


class Config(BaseSettings):
    """
    Global configuration using Pydantic Settings.
    Loads from:
    1. Environment variables (from .env or .prod.env)
    2. YAML files (global_config.yaml, production_config.yaml, .global_config.yaml)
    """

    model_config = SettingsConfigDict(
        # Load from .env file (will be handled separately for .prod.env)
        env_file=str(root_dir / ".env"),
        env_file_encoding="utf-8",
        # Allow nested env vars with double underscore
        env_nested_delimiter="__",
        # Case sensitive for field names
        case_sensitive=False,
        # Allow extra fields from YAML
        extra="allow",
    )

    # Top-level fields
    model_name: str
    dot_global_config_health_check: bool
    example_parent: ExampleParent
    default_llm: DefaultLlm
    llm_config: LlmConfig
    logging: LoggingConfig
    features: FeaturesConfig = Field(default_factory=lambda: FeaturesConfig())
    telemetry: TelemetryConfig = Field(default_factory=lambda: TelemetryConfig())
    cli: CliConfig = Field(default_factory=lambda: CliConfig())
    server: ServerConfig = Field(default_factory=lambda: ServerConfig())
    branding: BrandingConfig = Field(default_factory=lambda: BrandingConfig())
    subscription_config: SubscriptionConfig = Field(
        default_factory=lambda: SubscriptionConfig()
    )
    rate_limit: RateLimitConfig = Field(default_factory=lambda: RateLimitConfig())
    payments: AgenticPaymentsConfig = Field(
        default_factory=lambda: AgenticPaymentsConfig()
    )
    ask: AskConfig = Field(default_factory=lambda: AskConfig())
    web_bot_auth: WebBotAuthConfig = Field(default_factory=lambda: WebBotAuthConfig())
    gmail: GmailConfig = Field(default_factory=lambda: GmailConfig())
    pdf_forms: PdfFormsConfig = Field(default_factory=lambda: PdfFormsConfig())

    # Environment variables
    DEV_ENV: str
    OPENAI_API_KEY: str | None = None
    ANTHROPIC_API_KEY: str | None = None
    GROQ_API_KEY: str | None = None
    PERPLEXITY_API_KEY: str | None = None
    GEMINI_API_KEY: str | None = None

    # Agentic payments (observability only - the protocol reads these
    # via os.getenv() using the env var name from payments.yaml config,
    # not from these fields directly)
    X402_WALLET_ADDRESS: str | None = None
    X402_PRIVATE_KEY: str | None = None

    # Database & auth secrets
    BACKEND_DB_URI: str | None = None
    WORKOS_CLIENT_ID: str | None = None
    WORKOS_API_KEY: str | None = None
    # AuthKit issuer URL (https://<env>.authkit.app); enables OAuth 2.1 on /mcp
    WORKOS_AUTHKIT_DOMAIN: str | None = None
    # Canonical public URL of the /mcp endpoint (RFC 8707 resource identifier);
    # must match the resource indicator configured in the WorkOS dashboard
    MCP_PUBLIC_URL: str | None = None
    # Canonical public base URL of the HTTP API (no trailing slash, e.g.
    # https://api.example.com). When set, it is advertised as the `servers`
    # entry in the published OpenAPI spec so codegen / Swagger "Try it out" and
    # the landing-page API reference target the right host. Unset -> relative.
    API_PUBLIC_URL: str | None = None
    # Web Bot Auth signing identity: base64url-encoded 32-byte Ed25519 private
    # key seed. When set, /.well-known/http-message-signatures-directory
    # publishes the matching public key as a JWK Set; unset -> the route 404s.
    WEB_BOT_AUTH_PRIVATE_KEY: str | None = None
    SESSION_SECRET_KEY: str = "change-me-in-production"

    # Stripe & billing
    STRIPE_SECRET_KEY: str | None = None
    STRIPE_TEST_SECRET_KEY: str | None = None
    STRIPE_WEBHOOK_SECRET: str | None = None
    STRIPE_TEST_WEBHOOK_SECRET: str | None = None
    STRIPE_ALLOW_LIVE_KEY_IN_DEV: bool = False
    FRONTEND_URL: str = "http://localhost:3000"

    # Redis
    REDIS_URL: str | None = None

    # Google OAuth (Gmail integration) - fill in real values in .env
    GOOGLE_CLIENT_ID: str | None = None
    GOOGLE_CLIENT_SECRET: str | None = None
    GOOGLE_REDIRECT_URI: str | None = None
    # Base64-url Fernet key used to encrypt stored refresh tokens
    GOOGLE_TOKEN_ENC_KEY: str | None = None

    # Gmail push notifications (Pub/Sub) + outbound webhook fan-out.
    # All optional; if GMAIL_PUBSUB_TOPIC is unset the push pipeline stays
    # dormant (no watch auto-start, no runner loop).
    # Fully-qualified Pub/Sub topic, e.g. "projects/<proj>/topics/<topic>".
    GMAIL_PUBSUB_TOPIC: str | None = None
    # OIDC "aud" claim the push receiver requires on the Pub/Sub JWT.
    GMAIL_PUSH_AUDIENCE: str | None = None
    # OIDC "email" claim the push receiver requires (the push subscription's
    # service account). Empty disables the identity check (dev only).
    GMAIL_PUSH_SA_EMAIL: str | None = None
    # How the periodic runner (watch renewal + outbox drain) is driven:
    #   "off"      - no runner (default)
    #   "loop"     - in-process asyncio loop started in the FastAPI lifespan
    #   "endpoint" - driven externally by POSTing the internal /renew route
    WEBHOOK_RUNNER_MODE: str = "off"
    # Seconds between in-process runner ticks when WEBHOOK_RUNNER_MODE="loop".
    WEBHOOK_RUNNER_INTERVAL_S: int = 30
    # Max delivery attempts before an outbox row is marked "failed".
    WEBHOOK_MAX_ATTEMPTS: int = 6
    # Shared bearer required by the internal POST /api/v1/google/internal/renew
    # endpoint (WEBHOOK_RUNNER_MODE="endpoint"). Unset -> endpoint disabled.
    WEBHOOK_RUNNER_TOKEN: str | None = None

    # Runtime environment (computed via default_factory)
    is_local: bool = Field(
        default_factory=lambda: os.getenv("GITHUB_ACTIONS") != "true"
    )
    running_on: str = Field(
        default_factory=lambda: (
            "🖥️  local" if os.getenv("GITHUB_ACTIONS") != "true" else "☁️  CI"
        )
    )

    @model_validator(mode="after")
    def _require_secret_in_prod(self) -> "Config":
        if (
            self.DEV_ENV == "prod"
            and self.SESSION_SECRET_KEY == "change-me-in-production"
        ):
            raise ValueError(
                "SESSION_SECRET_KEY must be set to a strong random value in production"
            )
        return self

    @model_validator(mode="after")
    def _require_mcp_public_url_with_authkit(self) -> "Config":
        # Strip stray whitespace from .env values (it would break exact
        # issuer/audience matching) and normalize blank values to None so
        # they can't bypass the production check below.
        if self.WORKOS_AUTHKIT_DOMAIN is not None:
            self.WORKOS_AUTHKIT_DOMAIN = self.WORKOS_AUTHKIT_DOMAIN.strip() or None
        if self.MCP_PUBLIC_URL is not None:
            self.MCP_PUBLIC_URL = self.MCP_PUBLIC_URL.strip() or None
        # Same normalization for the API host: a trailing space would produce a
        # broken servers[0].url in the published OpenAPI spec.
        if self.API_PUBLIC_URL is not None:
            self.API_PUBLIC_URL = self.API_PUBLIC_URL.strip() or None
        # Tokens are audience-bound to MCP_PUBLIC_URL; without it the resource
        # URI falls back to localhost and OAuth silently breaks in production.
        if (
            self.DEV_ENV == "prod"
            and self.WORKOS_AUTHKIT_DOMAIN
            and not self.MCP_PUBLIC_URL
        ):
            raise ValueError(
                "MCP_PUBLIC_URL must be set when WORKOS_AUTHKIT_DOMAIN is "
                "enabled in production"
            )
        return self

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """
        Customize the priority order of settings sources.
        Priority (highest to lowest):
        1. Environment variables
        2. .env file
        3. YAML files (custom .global_config.yaml > production_config.yaml > global_config.yaml)
        4. Init settings (passed to constructor)
        """
        yaml_source = YamlSettingsSource(settings_cls)
        # Capture the merged YAML data once, here, where it is already computed
        # to populate the model - so to_yaml_dict() reuses it instead of
        # re-reading disk on every call and diverging from this singleton.
        global _yaml_config_cache  # noqa: PLW0603
        _yaml_config_cache = yaml_source.yaml_data
        return (
            env_settings,
            dotenv_settings,
            yaml_source,
            init_settings,
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert config to dictionary.

        Includes environment-sourced fields (secrets). Internal use only - do
        NOT expose the result to any transport. Anything user-facing must use
        :meth:`to_yaml_dict`.
        """
        return self.model_dump()

    def to_yaml_dict(self) -> dict[str, Any]:
        """Return only the YAML-sourced configuration, never ``.env`` secrets.

        The merged YAML layer (``global_config.yaml`` + split configs + the
        production overlay + ``.global_config.yaml``), captured once when this
        singleton was built. Every secret (API keys, ``BACKEND_DB_URI``,
        ``GOOGLE_TOKEN_ENC_KEY``, ``SESSION_SECRET_KEY``, ...) loads from the
        environment via a *different* settings source, so secrets are absent
        here by construction - this can never disclose one, even if a new secret
        field is added later. This is the safe view for the ``config_show`` /
        ``config_get`` services, reachable over the authenticated HTTP transport.

        Note: this is the YAML *layer*, not the effective config. Pydantic
        defaults for fields absent from every YAML file, and non-secret env-var
        overrides (e.g. ``LLM_CONFIG__CACHE_ENABLED``), are intentionally not
        reflected. Callers needing effective runtime values must not use this.
        A fresh copy is returned so callers cannot mutate the cached view.
        """
        return deepcopy(_yaml_config_cache)

    def _identify_provider(self, model_name: str) -> str:
        """Identify the LLM provider from a model name string."""
        name_lower = model_name.lower()
        if "gpt" in name_lower or re.match(OPENAI_O_SERIES_PATTERN, name_lower):
            return "openai"
        if "claude" in name_lower or "anthropic" in name_lower:
            return "anthropic"
        if "groq" in name_lower:
            return "groq"
        if "perplexity" in name_lower:
            return "perplexity"
        if "gemini" in name_lower:
            return "gemini"
        return "unknown"

    def llm_api_key(self, model_name: str | None = None) -> str:
        """Returns the appropriate API key based on the model name."""
        model_identifier = model_name or self.model_name
        provider = self._identify_provider(model_identifier)
        api_keys = {
            "openai": self.OPENAI_API_KEY,
            "anthropic": self.ANTHROPIC_API_KEY,
            "groq": self.GROQ_API_KEY,
            "perplexity": self.PERPLEXITY_API_KEY,
            "gemini": self.GEMINI_API_KEY,
        }
        if provider in api_keys:
            key = api_keys[provider]
            if key is None:
                raise ValueError(
                    f"API key for provider '{provider}' is not configured. "
                    f"Set {provider.upper()}_API_KEY in your .env file."
                )
            return key
        raise ValueError(f"No API key configured for model: {model_identifier}")


# Load .env files before creating the config instance
# Load .env file first, to get DEV_ENV if it's defined there.
# override=False so real environment variables keep priority over .env,
# matching the source order documented in settings_customise_sources.
load_dotenv(dotenv_path=root_dir / ".env", override=False)

# Now, check DEV_ENV and load .prod.env if it's 'prod', overriding .env
if os.getenv("DEV_ENV") == "prod":
    load_dotenv(dotenv_path=root_dir / ".prod.env", override=True)

# Check if .env file has been properly loaded
is_local = os.getenv("GITHUB_ACTIONS") != "true"
if is_local:
    env_file_to_check = ".prod.env" if os.getenv("DEV_ENV") == "prod" else ".env"
    env_values = dotenv_values(root_dir / env_file_to_check)
    if not env_values:
        warnings.warn(
            f"{env_file_to_check} file not found or empty",
            UserWarning,
            stacklevel=2,
        )

# Create a singleton instance
# Note: Config() loads all required fields from YAML and .env files via custom settings sources
global_config = Config()  # ty: ignore[missing-argument]
