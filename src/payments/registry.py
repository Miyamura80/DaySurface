"""Protocol registry for agentic payment protocols."""

import threading

from loguru import logger as log

from src.payments.base import PaymentProtocol
from src.payments.types import PaymentProtocolName


class PaymentRegistry:
    """Singleton registry of enabled payment protocols.

    Lazy-imports protocol modules only when enabled in config to avoid
    importing SDK code at module load time. Note: x402 is currently a
    hard dependency (always installed); the lazy-import pattern avoids
    import-time side effects and keeps startup fast when the protocol
    is disabled. Future protocols (MPP, ACP) may use optional deps.
    """

    _instance: "PaymentRegistry | None" = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._protocols: dict[str, PaymentProtocol] = {}
        self._initialized = False

    @classmethod
    def get(cls) -> "PaymentRegistry":
        """Return the singleton registry instance."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def initialize(self) -> None:
        """Load enabled protocols from config. Called once at startup."""
        if self._initialized:
            return

        with self._lock:
            if self._initialized:
                return

            # Lazy by design (see class docstring): config and protocol modules
            # are imported only when needed so module import stays side-effect
            # free and startup stays fast when payments are disabled.
            from common import global_config  # noqa: PLC0415

            cfg = global_config.payments

            if cfg.x402.enabled:
                # Lazy by design (see class docstring): import the protocol
                # module only when the protocol is enabled in config.
                from src.payments.x402.protocol import X402Protocol  # noqa: PLC0415

                self._protocols[PaymentProtocolName.X402] = X402Protocol(cfg.x402)
                log.info("x402 protocol registered")

            if cfg.mpp.enabled:
                log.info("MPP protocol enabled but not yet implemented")

            if cfg.acp.enabled:
                log.info("ACP protocol enabled but not yet implemented")

            enabled = self.list_enabled()
            if enabled:
                log.info("Agentic payments initialized: {}", ", ".join(enabled))
            else:
                log.debug("No agentic payment protocols enabled")

            self._initialized = True

    def get_protocol(self, name: str) -> PaymentProtocol | None:
        """Get a registered protocol by name. Returns None if not found."""
        return self._protocols.get(name)

    def list_enabled(self) -> list[str]:
        """Return names of all registered protocols."""
        return list(self._protocols.keys())

    def is_any_enabled(self) -> bool:
        """Whether at least one protocol is registered."""
        return len(self._protocols) > 0

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton for testing."""
        with cls._lock:
            if cls._instance is not None:
                for proto in cls._instance._protocols.values():
                    proto.shutdown()
                cls._instance = None
