# Agentic Payments Integration Plan

## Overview

This document describes the architecture for integrating multiple agentic payment protocols into the project. Each protocol is config-driven (enabled/disabled via `global_config.yaml`) and lives under `src/payments/`.

## Protocols

### 1. x402 (Coinbase)  - Priority: First

- **Type:** HTTP 402-based stablecoin micropayments
- **Status:** Fully open, no gating, Python SDK on PyPI (`x402` v2.5.0)
- **Testnet:** Base Sepolia + Solana Devnet (free, no real crypto needed)
- **MCP integrations exist:** MetaMask/mcp-x402, Vercel x402-mcp, @civic/x402-mcp, official guide
- **New dep:** `x402[httpx,fastapi]>=2.5.0`

### 2. MPP (Stripe/Tempo Machine Payments Protocol)  - Priority: Second

- **Type:** HTTP 402-based, stablecoins + card rails + Bitcoin Lightning
- **Status:** Developer preview (`2026-03-04.preview`), gated access request required
- **SDK:** TypeScript only (`mppx`). No Python SDK  - we build a custom `httpx` client.
- **New dep:** None (httpx already transitive)

### 3. ACP (OpenAI/Stripe Agentic Commerce Protocol)  - Priority: Third

- **Type:** Merchant-side REST endpoints per OpenAPI spec
- **Status:** Spec is Apache 2.0 open. Distribution through ChatGPT is gated.
- **SDK:** No first-party SDK. Implement REST/MCP endpoints from OpenAPI spec.
- **New dep:** None (FastAPI + Stripe already present)

## File Structure

```
src/payments/
    __init__.py              # Public API: get_protocol(), list_enabled_protocols()
    base.py                  # ABC: BasePaymentProtocol
    types.py                 # Shared types (PaymentRequest, PaymentResult, ProtocolName)
    registry.py              # Discovers + instantiates enabled protocols from config
    x402/
        __init__.py
        protocol.py          # X402Protocol(BasePaymentProtocol)
        config.py            # x402-specific pydantic models
    mpp/
        __init__.py
        protocol.py          # MppProtocol(BasePaymentProtocol)
        config.py            # MPP-specific pydantic models
        client.py            # Custom httpx client (no Python SDK exists)
    acp/
        __init__.py
        protocol.py          # AcpProtocol(BasePaymentProtocol)
        config.py            # ACP-specific pydantic models
        endpoints.py         # ACP REST endpoint handlers per OpenAPI spec
```

## Core Abstractions

### BasePaymentProtocol (ABC)

```python
class BasePaymentProtocol(ABC):

    @abstractmethod
    def protocol_name(self) -> ProtocolName: ...

    @abstractmethod
    async def initialize(self) -> None:
        """One-time setup (SDK init, key validation). Called by registry."""

    @abstractmethod
    async def create_payment_requirement(
        self,
        resource_url: str,
        amount: Decimal,
        currency: str,
        description: str,
        metadata: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Build the 402 response payload or equivalent offer."""

    @abstractmethod
    async def verify_payment(
        self,
        payment_payload: dict[str, Any],
    ) -> PaymentResult:
        """Verify an incoming payment header/payload."""

    @abstractmethod
    async def health_check(self) -> bool:
        """Return True if external dependencies are reachable."""

    def shutdown(self) -> None:
        """Optional cleanup. Override if the protocol holds connections."""
```

### Shared Types

```python
class ProtocolName(StrEnum):
    X402 = "x402"
    MPP = "mpp"
    ACP = "acp"

class PaymentStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"

@dataclass(frozen=True)
class PaymentRequest:
    resource_url: str
    amount: Decimal
    currency: str
    description: str
    metadata: dict[str, str]

@dataclass(frozen=True)
class PaymentResult:
    protocol: ProtocolName
    status: PaymentStatus
    transaction_id: str | None
    raw_response: dict[str, Any]
```

### Protocol Registry

```python
class ProtocolRegistry:
    def __init__(self, config: PaymentsConfig) -> None: ...
    async def initialize_all(self) -> None: ...
    def get(self, name: ProtocolName) -> BasePaymentProtocol: ...
    def list_enabled(self) -> list[ProtocolName]: ...
    async def shutdown_all(self) -> None: ...
```

Lazy-imports only enabled protocol submodules to avoid pulling unused SDK dependencies.

## Configuration

### global_config.yaml addition

```yaml
########################################################
# Agentic Payments
########################################################
payments:
  x402:
    enabled: false
    network: "base-sepolia"
    facilitator_url: "https://facilitator.x402.org"
    wallet_private_key_env: "X402_WALLET_PRIVATE_KEY"
    max_amount_per_request: 10.0
  mpp:
    enabled: false
    api_version: "2026-03-04.preview"
    tempo_rpc_url: "https://rpc.tempo.xyz"
    stripe_mpp_mode: true
    wallet_private_key_env: "MPP_WALLET_PRIVATE_KEY"
    max_amount_per_request: 100.0
  acp:
    enabled: false
    merchant_name: ""
    merchant_url: ""
    webhook_secret_env: "ACP_WEBHOOK_SECRET"
    supported_payment_methods:
      - "card"
    stripe_account_id_env: "STRIPE_ACCOUNT_ID"
```

### Config Models (additions to config_models.py)

```python
class X402Config(BaseModel):
    enabled: bool = False
    network: str = "base-sepolia"
    facilitator_url: str = "https://facilitator.x402.org"
    wallet_private_key_env: str = "X402_WALLET_PRIVATE_KEY"
    max_amount_per_request: float = 10.0

class MppConfig(BaseModel):
    enabled: bool = False
    api_version: str = "2026-03-04.preview"
    tempo_rpc_url: str = "https://rpc.tempo.xyz"
    stripe_mpp_mode: bool = True
    wallet_private_key_env: str = "MPP_WALLET_PRIVATE_KEY"
    max_amount_per_request: float = 100.0

class AcpConfig(BaseModel):
    enabled: bool = False
    merchant_name: str = ""
    merchant_url: str = ""
    webhook_secret_env: str = "ACP_WEBHOOK_SECRET"
    supported_payment_methods: list[str] = ["card"]
    stripe_account_id_env: str = "STRIPE_ACCOUNT_ID"

class PaymentsConfig(BaseModel):
    x402: X402Config = Field(default_factory=X402Config)
    mpp: MppConfig = Field(default_factory=MppConfig)
    acp: AcpConfig = Field(default_factory=AcpConfig)
```

### global_config.py addition

```python
payments: PaymentsConfig = Field(default_factory=lambda: PaymentsConfig())
```

## Design Decisions

1. **Secrets via env var indirection**  - Config stores the env var *name* (e.g., `wallet_private_key_env: "X402_WALLET_PRIVATE_KEY"`), never the key itself in YAML.

2. **Registry pattern**  - `ProtocolRegistry` reads `global_config.payments` at startup, lazy-imports only enabled protocols, calls `initialize()` on each. Single entry point for the API server.

3. **Base ABC does not unify checkout flows**  - ACP's merchant-of-record model is fundamentally different from x402/MPP's pay-per-request model. The `create_payment_requirement` / `verify_payment` pair captures the common 402 loop. ACP additionally implements merchant endpoint handlers.

4. **MPP custom HTTP client**  - No Python SDK exists. `mpp/client.py` is a thin `httpx.AsyncClient` wrapper implementing the MPP REST API per the spec.

5. **Extensibility**  - Adding a new protocol (AP2, UCP, etc.) requires:
   - Create `src/payments/<name>/` with `protocol.py` + `config.py`
   - Implement `BasePaymentProtocol`
   - Add config model to `PaymentsConfig`
   - Add YAML section under `payments:`
   - No changes to `base.py`, `registry.py`, or `types.py` needed.

## API Server Integration

New file: `api_server/routes/agentic_payments.py`

```python
router = APIRouter(prefix="/api/v1/agentic-payments", tags=["agentic-payments"])

# x402/MPP: 402 gating for protected resources
# ACP: Merchant endpoint implementations (checkout sessions, orders, webhooks)
# Health: list enabled protocols + status
```

Registered in `api_server/server.py` alongside existing routes.

## Implementation Order

1. Config models + YAML section + wire into `global_config.py`
2. Core abstractions (`types.py`, `base.py`, `registry.py`, `__init__.py`)
3. x402 protocol (has Python SDK, fully open, testnet available)
4. MPP protocol (custom client, developer preview)
5. ACP protocol (open spec, uses existing Stripe)
6. API routes + server registration
7. Tests (`tests/test_payments/`)
