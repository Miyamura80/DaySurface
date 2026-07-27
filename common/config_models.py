"""
Pydantic models for global configuration structure.

This module defines all the nested configuration models used by the Config class.
Each model corresponds to a section in the global_config.yaml file and provides
type validation and structure for the configuration data.
"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ExampleParent(BaseModel):
    """Example configuration parent model."""

    example_child: str


class DefaultLlm(BaseModel):
    """Default LLM configuration."""

    default_model: str
    fallback_model: str | None = None
    default_temperature: float
    default_max_tokens: int


class RetryConfig(BaseModel):
    """Retry configuration for LLM requests."""

    max_attempts: int
    min_wait_seconds: int
    max_wait_seconds: int


class LlmConfig(BaseModel):
    """LLM configuration including caching and retry settings."""

    cache_enabled: bool
    retry: RetryConfig


class LoggingLocationConfig(BaseModel):
    """Location information display configuration for logging."""

    enabled: bool
    show_file: bool
    show_function: bool
    show_line: bool
    show_for_info: bool
    show_for_debug: bool
    show_for_warning: bool
    show_for_error: bool


class LoggingFormatConfig(BaseModel):
    """Logging format configuration."""

    show_time: bool
    show_session_id: bool
    location: LoggingLocationConfig


class LoggingLevelsConfig(BaseModel):
    """Logging level configuration."""

    debug: bool
    info: bool
    warning: bool
    error: bool
    critical: bool


class RedactionPattern(BaseModel):
    """Configuration for a specific redaction pattern."""

    name: str
    regex: str
    placeholder: str


class RedactionConfig(BaseModel):
    """Configuration for log redaction/scrubbing."""

    enabled: bool = True
    use_default_pii: bool = True
    patterns: list[RedactionPattern] = []


class LoggingConfig(BaseModel):
    """Complete logging configuration."""

    verbose: bool
    format: LoggingFormatConfig
    levels: LoggingLevelsConfig
    redaction: RedactionConfig = Field(default_factory=lambda: RedactionConfig())


class TelemetryConfig(BaseModel):
    """Telemetry configuration."""

    enabled: bool = True
    endpoint: str | None = None


class CliConfig(BaseModel):
    """CLI configuration."""

    default_format: str = "table"
    interactive_fallback: bool = True
    check_updates_on_start: bool = False
    emoji: str = ""
    primary_color: str = "cyan"
    secondary_color: str = "green"


class ServerConfig(BaseModel):
    """API server configuration."""

    host: str = "0.0.0.0"
    port: int = 8080
    allowed_origins: list[str] = ["http://localhost:3000"]
    # Streamable-HTTP session mode for the /mcp mount. True (default) keeps
    # every request self-contained - required for multi-replica / spin-down
    # deployments - but makes in-band elicitation impossible (the client's
    # elicitation response POST cannot be correlated back to the transient
    # session, so ctx.elicit() hangs; empirically verified, same failure mode
    # as python-sdk issue 678 for sampling). Single-replica deployments may
    # set False to enable host-native elicitation (e.g. the signing
    # confirmation dialog) at the cost of in-memory session affinity.
    mcp_stateless_http: bool = True


class IconConfig(BaseModel):
    """A sized icon for pre-connect branding (MCP icons spec).

    ``src`` must be an HTTPS URL. ``sizes`` accepts ``WxH`` entries or
    ``"any"`` (for SVG); ``theme`` is ``"light"``/``"dark"`` when an icon is
    tuned for one mode.
    """

    src: str
    mime_type: str = "image/svg+xml"
    sizes: list[str] = Field(default_factory=lambda: ["any"])
    theme: Literal["light", "dark"] | None = None


class BrandingConfig(BaseModel):
    """Pre-connect registry branding (SEP-2127 Server Card / MCP registry).

    Single source of truth for the server's public identity: the reverse-DNS
    ``name`` a registry indexes by, and the ``title``/``description``/``icons``
    a client shows in its "add server" UI *before* anyone connects. Surfaced
    verbatim at ``/.well-known/mcp/server-card.json``. The deployed MCP endpoint
    URL is not configured here - it is derived from ``MCP_PUBLIC_URL`` so the
    card and the OAuth resource metadata can never disagree.
    """

    name: str = "io.github.Miyamura80/MCP-Template"
    title: str = "GmailMCP"
    description: str = (
        "Give your AI agent real tools - one service registry over CLI, MCP, and HTTP."
    )
    website_url: str = "https://gmailmcp.com"
    repository_url: str = "https://github.com/Miyamura80/MCP-Template"
    repository_source: str = "github"
    icons: list[IconConfig] = Field(
        default_factory=lambda: [IconConfig(src="https://gmailmcp.com/favicon.svg")]
    )


class WebBotAuthConfig(BaseModel):
    """Web Bot Auth signing-key directory configuration.

    Drives ``/.well-known/http-message-signatures-directory``
    (draft-meunier-http-message-signatures-directory), which publishes this
    agent's Ed25519 public signing key(s) as a JWK Set so origins can verify
    HTTP Message Signatures it sends. The private key is supplied out of band
    via the ``WEB_BOT_AUTH_PRIVATE_KEY`` secret (a base64url-encoded 32-byte
    Ed25519 seed); when that is unset the route returns 404, cleanly signalling
    "no signing identity" instead of advertising an empty directory.

    ``key_lifetime_days`` sets the per-key ``exp`` (relative to first publish);
    ``nbf`` is the publish time.
    """

    key_lifetime_days: int = Field(default=365, ge=1)


class RateLimitConfig(BaseModel):
    """Rate limiting configuration."""

    enabled: bool = True
    trust_proxy_headers: bool = False
    tiers: dict[str, dict[str, int]] = Field(
        default_factory=lambda: {
            "free_tier": {"rps": 2, "rpm": 30, "rph": 200, "rpd": 100},
            "plus_tier": {"rps": 10, "rpm": 120, "rph": 5000, "rpd": 10000},
            "default": {"rps": 5, "rpm": 60, "rph": 1000, "rpd": 5000},
        }
    )


class StripeConfig(BaseModel):
    """Stripe billing configuration."""

    price_ids: dict[str, str] = Field(default_factory=lambda: {"test": "", "prod": ""})
    meter_event_name: str = "api_requests"
    api_version: str = "2025-03-31.basil"


class MeteredConfig(BaseModel):
    """Metered billing configuration."""

    included_units: int = 100
    overage_unit_amount: int = 1
    unit_label: str = "API calls"


class PaymentRetryConfig(BaseModel):
    """Payment retry configuration."""

    max_attempts: int = 3


class TierLimitsConfig(BaseModel):
    """Tier limit configuration."""

    daily_requests: int = 100


class SubscriptionConfig(BaseModel):
    """Subscription/billing configuration."""

    tier_limits: dict[str, TierLimitsConfig] = Field(
        default_factory=lambda: {
            "free_tier": TierLimitsConfig(daily_requests=100),
            "plus_tier": TierLimitsConfig(daily_requests=10000),
        }
    )
    default_tier: str = "free_tier"
    stripe: StripeConfig = Field(default_factory=StripeConfig)
    metered: MeteredConfig = Field(default_factory=MeteredConfig)
    trial_period_days: int = 7
    payment_retry: PaymentRetryConfig = Field(default_factory=PaymentRetryConfig)


class X402ProtocolConfig(BaseModel):
    """x402 (Coinbase) protocol configuration."""

    enabled: bool = False
    facilitator_url: str = "https://x402.org/facilitator"
    network: str = "base-sepolia"
    wallet_address_env: str = "X402_WALLET_ADDRESS"
    private_key_env: str = "X402_PRIVATE_KEY"
    default_amount: str = "0.001"
    default_asset: str = "USDC"
    testnet: bool = True


class MppProtocolConfig(BaseModel):
    """MPP (Stripe/Tempo) protocol configuration."""

    enabled: bool = False


class AcpProtocolConfig(BaseModel):
    """ACP (OpenAI/Stripe) protocol configuration."""

    enabled: bool = False


class AgenticPaymentsConfig(BaseModel):
    """Top-level agentic payments configuration."""

    x402: X402ProtocolConfig = Field(default_factory=X402ProtocolConfig)
    mpp: MppProtocolConfig = Field(default_factory=MppProtocolConfig)
    acp: AcpProtocolConfig = Field(default_factory=AcpProtocolConfig)


class AskConfig(BaseModel):
    """NLWeb ``/ask`` Q&A endpoint configuration."""

    enabled: bool = False
    corpus_path: str = "docs/content/docs"
    docs_base_url: str = "https://docs.gmailmcp.com"
    top_k: int = 5
    rate_limit_per_minute: int = 20


class FeaturesConfig(BaseModel):
    """Feature flags configuration."""

    model_config = {"extra": "allow"}  # Allow arbitrary flags


class GmailConfig(BaseModel):
    """Gmail integration configuration."""

    # Ceiling (in decoded bytes) on a single attachment fetched via
    # ``gmail_get_attachment``. That tool returns the file's base64 straight
    # into the model's context, so an unbounded fetch of a large file re-creates
    # the very context-bloat the lean thread payload was designed to avoid.
    # Deliberately set absurdly high for now (~1 TiB = effectively unbounded) so
    # nothing is rejected until we pick a real limit; tighten this later.
    max_attachment_bytes: int = Field(
        default=1_099_511_627_776,
        ge=0,
        description=(
            "Max decoded size (bytes) of a single attachment returned by "
            "gmail_get_attachment before it is rejected. Effectively unbounded "
            "by default; lower it to protect the model's context window."
        ),
    )


class PdfSigningConfig(BaseModel):
    """Server-held sealing certificate for user-gated PDF signing.

    The seal is a platform certificate (DocuSign-style), not a per-user
    identity. In production point both paths at a real key pair; when unset in
    dev, a self-signed certificate is generated once and cached under
    ``dev_cert_dir``.
    """

    cert_path: str | None = Field(
        default=None, description="PEM certificate used for the PAdES seal."
    )
    key_path: str | None = Field(
        default=None, description="PEM private key matching cert_path."
    )
    dev_cert_dir: str = Field(
        default=".pdf_signing",
        description=(
            "Directory (relative to repo root) where the auto-generated dev "
            "self-signed certificate is cached when cert_path is unset."
        ),
    )

    @model_validator(mode="after")
    def _cert_pair_complete(self) -> "PdfSigningConfig":
        # A partial pair would silently fall back to the untrusted dev cert -
        # a production deployment must find out at startup, not in a signed
        # document's validation report.
        if bool(self.cert_path) != bool(self.key_path):
            raise ValueError(
                "pdf_forms.signing: cert_path and key_path must be set "
                "together (or both left null for the dev self-signed cert)."
            )
        return self


class PdfFormsConfig(BaseModel):
    """PDF form-filling and signing configuration (loaded from pdf_forms.yaml)."""

    max_document_bytes: int = Field(
        default=20 * 1024 * 1024,
        ge=0,
        description="Max size (decoded bytes) of a PDF accepted into a doc session.",
    )
    text_layout_max_lines: int = Field(
        default=400,
        ge=0,
        description=(
            "Cap on extracted text-layout lines returned by pdf_open for flat "
            "PDFs, keeping tool responses bounded."
        ),
    )
    render_dpi: int = Field(
        default=110,
        ge=18,
        le=300,
        description="Rasterization DPI for render_pages page images.",
    )
    render_max_pages: int = Field(
        default=4,
        ge=1,
        description="Max pages a single render_pages request may rasterize.",
    )
    session_ttl_hours: int = Field(
        default=72,
        ge=1,
        description="Age after which unexported document sessions may be swept.",
    )
    signing: PdfSigningConfig = Field(default_factory=PdfSigningConfig)
