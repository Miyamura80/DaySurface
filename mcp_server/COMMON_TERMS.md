# MCP Common Terms

Glossary of frequently confused **Model Context Protocol** terms. Consult this
before naming or designing new MCP-related code.

> ⚠️ **Spec baseline: 2025-11-25. Last reviewed: 2026-05-09.** MCP changes
> fast - recheck the [changelog](https://modelcontextprotocol.io/specification/2025-11-25/changelog)
> and [2026 roadmap](https://blog.modelcontextprotocol.io/posts/2026-mcp-roadmap/)
> if it has been more than ~1 month. 🟡 = in-flight, most likely to drift.

## 1. Host vs. Client vs. Server

- **Host** - user-facing AI app that owns the LLM (Claude Desktop, Cursor).
- **Client** - 1:1 protocol connector *inside* the Host, one per Server.
- **Server** - external program exposing data/capabilities (this repo's `mcp_server/`).

One Host → many Clients → each talks to one Server. "MCP client" in blog
posts often means the Host - clarify which.

## 2. Tools vs. Resources vs. Prompts (server primitives)

| Primitive | Controlled by | Side effects | Use for           |
|-----------|---------------|--------------|-------------------|
| Tool      | Model         | Yes          | Function calls    |
| Resource  | Application   | No (read)    | Background context|
| Prompt    | User          | No           | Slash-command templates |
| **Tasks** 🟡 | Model     | Yes (async)  | Long-running ops (SEP-1686, experimental in 2025-11-25) |

Decide by: mutates state → Tool; app injects as context → Resource; user
picks from menu → Prompt; long-running → Tasks. Don't expose read-only
data as a Tool.

## 3. Sampling, Roots, Elicitation (client primitives)

Server→client calls:

- **Sampling** - Server asks the Host's LLM to generate a completion.
- **Roots** - Client tells Server which URIs it's scoped to. Any valid URI
  (commonly `file://`); advisory boundary the server SHOULD respect, **not**
  enforced - auth is separate. Distinct from Resources (server-exposed data,
  not client-declared scope).
- **Elicitation** - Server asks the user for structured input via JSON
  Schema. Stable as of 2025-11-25.

## 4. MCP Apps

The [ext-apps](https://github.com/modelcontextprotocol/ext-apps) extension:
interactive iframe-sandboxed UIs (HTML resources via `ui://` URIs) embedded
in chat clients, with bidirectional `postMessage` / JSON-RPC communication.
*Not* a synonym for "MCP application" - Claude Desktop is a Host, your
backend is a Server, neither is an "App".
([OpenAI Apps SDK](https://developers.openai.com/apps-sdk/concepts/mcp-server))

## 5. Transports

- **stdio** - Server runs **locally on the Host machine** as a child process
  spawned by the Client. No network. Use for IDE plugins, CLI tools,
  per-user local integrations.
- **Streamable HTTP** - Server runs **remotely**, reached over the network;
  one HTTP endpoint with optional SSE streaming, OAuth 2.1. Use for hosted/
  multi-tenant servers.
- **SSE** - *deprecated* legacy two-endpoint setup. Don't use for new servers.

## 6. JSON-RPC handshake

1. Client → `initialize` (protocolVersion, capabilities, clientInfo).
2. Server → response with its protocolVersion + capabilities.
3. Client → `notifications/initialized`.

**Capabilities** are negotiated feature flags - don't call methods whose
capability wasn't advertised. Mismatched `protocolVersion` terminates the
connection. Streamable HTTP also conveys it via `MCP-Protocol-Version` header.

## 7. OAuth 2.1 pitfalls

- MCP server is a **resource server**, not the authorization server (token factory).
  This template implements exactly that split: RFC 9728 Protected Resource
  Metadata at `/.well-known/oauth-protected-resource[/mcp]`
  (`api_server/routes/well_known.py`), a `resource_metadata` hint in the 401
  `WWW-Authenticate` challenge (`api_server/middleware/mcp_auth.py`), and
  audience-bound token validation against WorkOS AuthKit
  (`api_server/auth/authkit_auth.py`). AuthKit is the AS and handles
  CIMD/DCR/PKCE. Enable via `WORKOS_AUTHKIT_DOMAIN` + `MCP_PUBLIC_URL`.
- Sessions are anonymous unless wired through OAuth. Machine identity is
  now first-class (M2M client_credentials, Cross App Access).
- `resource` parameter must match the AS's expected URL **exactly** (trailing
  slash matters) - mismatches cause silent 401s. RFC 8707 is moving from
  SHOULD to **mandatory** in the 2026-03-15 draft.
- Use **CIMD** (Client ID Metadata Documents) for client registration;
  Dynamic Client Registration (DCR) is now MAY, not the default.
- 🟡 **Scopes:** prefer least privilege. The 2026-03-15 draft adds
  **incremental scope consent** - ask progressively, not upfront.
- 🟡 **Token storage:** persist tokens so users don't re-consent on each
  Host restart. Once SEP-1932 (DPoP) lands, you persist the *key*, not the
  bearer token.

## Sources

- [MCP Specification (2025-11-25)](https://modelcontextprotocol.io/specification/2025-11-25)
- [MCP Changelog](https://modelcontextprotocol.io/specification/2025-11-25/changelog)
- [2026 MCP Roadmap](https://blog.modelcontextprotocol.io/posts/2026-mcp-roadmap/)
- [MCP Architecture](https://modelcontextprotocol.io/docs/learn/architecture)
- [MCP Transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)
- [MCP Authorization](https://modelcontextprotocol.io/docs/tutorials/security/authorization)
- [WorkOS - Tools, Resources, Prompts, Sampling, Roots, Elicitation](https://workos.com/blog/mcp-features-guide)
- [MCPcat - Server vs Client vs Host](https://mcpcat.io/blog/mcp-server-client-host/)
- [fka.dev - Why MCP deprecated SSE](https://blog.fka.dev/blog/2025-06-06-why-mcp-deprecated-sse-and-go-with-streamable-http/)
- [Obsidian Security - OAuth pitfalls in MCP](https://www.obsidiansecurity.com/blog/when-mcp-meets-oauth-common-pitfalls-leading-to-one-click-account-takeover)
- [Auth0 - CIMD vs DCR for MCP](https://auth0.com/blog/cimd-vs-dcr-mcp-registration/)
