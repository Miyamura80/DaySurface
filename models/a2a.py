"""A2A (Agent2Agent) Agent Card contract - pure Pydantic models.

The Agent Card is A2A's pre-connect discovery document (the agent equivalent of
the MCP Server Card): a JSON file served at ``/.well-known/agent-card.json`` that
declares an agent's identity, transport endpoint, capabilities, and skills so
other agents and orchestrators can discover and interoperate with it.

Modelled against the A2A specification v0.3.0. The wire format is camelCase
(``protocolVersion``, ``defaultInputModes``, ...), so every model uses a
``serialization_alias`` and is dumped with ``by_alias=True``. The card is only
ever *built* (from config) and *served*, never parsed from the wire, so a
serialization-only alias keeps snake_case constructor arguments. Required fields
per the spec: ``protocolVersion``, ``name``, ``description``, ``url``,
``version``, ``capabilities``, ``defaultInputModes``, ``defaultOutputModes``,
``skills``.

This module is transport-agnostic and imports nothing from the app layers; the
route at ``api_server/routes/well_known.py`` assembles a card from the service
registry and branding config.
"""

from pydantic import BaseModel, Field

# A2A spec version this card conforms to. Pinned, not derived from the package
# version: it advertises the *protocol* contract, not the server's own version.
A2A_PROTOCOL_VERSION = "0.3.0"


class A2AAgentProvider(BaseModel):
    """The organization that published the agent (spec: AgentProvider)."""

    organization: str
    url: str


class A2AAgentCapabilities(BaseModel):
    """Optional protocol features the agent supports (spec: AgentCapabilities)."""

    streaming: bool = False
    push_notifications: bool = Field(
        default=False, serialization_alias="pushNotifications"
    )
    state_transition_history: bool = Field(
        default=False, serialization_alias="stateTransitionHistory"
    )


class A2AAgentInterface(BaseModel):
    """A (url, transport) pair the agent is reachable at (spec: AgentInterface)."""

    url: str
    transport: str


class A2AAgentSkill(BaseModel):
    """A discrete capability the agent can perform (spec: AgentSkill).

    Required: ``id``, ``name``, ``description``, ``tags``.
    """

    id: str
    name: str
    description: str
    tags: list[str] = Field(default_factory=list)
    examples: list[str] | None = None
    input_modes: list[str] | None = Field(
        default=None, serialization_alias="inputModes"
    )
    output_modes: list[str] | None = Field(
        default=None, serialization_alias="outputModes"
    )


class A2AAgentCard(BaseModel):
    """A2A Agent Card - the ``/.well-known/agent-card.json`` discovery document."""

    protocol_version: str = Field(
        default=A2A_PROTOCOL_VERSION, serialization_alias="protocolVersion"
    )
    name: str
    description: str
    url: str
    version: str
    capabilities: A2AAgentCapabilities = Field(default_factory=A2AAgentCapabilities)
    default_input_modes: list[str] = Field(serialization_alias="defaultInputModes")
    default_output_modes: list[str] = Field(serialization_alias="defaultOutputModes")
    skills: list[A2AAgentSkill]
    # Optional fields below.
    preferred_transport: str | None = Field(
        default=None, serialization_alias="preferredTransport"
    )
    additional_interfaces: list[A2AAgentInterface] | None = Field(
        default=None, serialization_alias="additionalInterfaces"
    )
    provider: A2AAgentProvider | None = None
    icon_url: str | None = Field(default=None, serialization_alias="iconUrl")
    documentation_url: str | None = Field(
        default=None, serialization_alias="documentationUrl"
    )

    def to_wire(self) -> dict:
        """Serialise to the camelCase JSON the A2A spec defines, dropping unset
        optional fields so the document stays minimal and schema-clean."""
        return self.model_dump(by_alias=True, exclude_none=True)
