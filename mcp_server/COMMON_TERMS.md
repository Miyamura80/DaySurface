# MCP Common Terms

Glossary of frequently confused **Model Context Protocol** terms. Consult this
before naming or designing new MCP-related code.

> ⚠️ **Spec baseline: 2026-07-28. Last reviewed: 2026-11-14.** MCP changes
> fast - recheck the [changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog)
> and the [2026-07-28 release post](https://blog.modelcontextprotocol.io/posts/2026-07-28/)
> if it has been more than ~1 month. 🟡 = in-flight, most likely to drift.
> SDK: `mcp` (python-sdk) 2.x, which serves 2026-07-28 *and* every earlier
> revision from one server (`mcp_server/server.py`).

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
| **Tasks** 🟡 | Model     | Yes (async)  | Long-running ops - now the `io.modelcontextprotocol/tasks` **extension** (SEP-2663), no longer core |

Decide by: mutates state → Tool; app injects as context → Resource; user
picks from menu → Prompt; long-running → Tasks. Don't expose read-only
data as a Tool.

## 3. Sampling, Roots, Elicitation (client primitives)

Things a server needs *from* the client. Until 2025-11-25 these were
server→client requests; **2026-07-28 removed server-initiated requests** and
replaced them with **MRTR** (below).

- **Sampling** - Server asks the Host's LLM to generate a completion.
  *Deprecated* (SEP-2577): call an LLM provider directly instead.
- **Roots** - Client tells Server which URIs it's scoped to. Any valid URI
  (commonly `file://`); advisory boundary the server SHOULD respect, **not**
  enforced - auth is separate. Distinct from Resources (server-exposed data,
  not client-declared scope). *Deprecated* (SEP-2577): pass paths as tool
  arguments or config.
- **Elicitation** - Server asks the user for structured input via JSON
  Schema (form mode) or sends them to a URL (URL mode, e.g. OAuth consent).
  Still core. 2026-07-28 dropped `elicitationId` and
  `notifications/elicitation/complete`: the client learns the outcome by
  retrying the original request.
- **MRTR (Multi Round-Trip Requests, SEP-2322)** - the 2026-07-28 way to
  gather that input: the server answers with an `InputRequiredResult`
  (`resultType: "input_required"`, `inputRequests`), the client retries the
  same request with `inputResponses` (and any opaque `requestState`). Every
  result now carries `resultType` (`"complete"` otherwise). In python-sdk 2.x:
  `Resolve(...)`/`Elicit(...)` tool parameters or a hand-returned
  `InputRequiredResult`; `ctx.elicit()` raises `NoBackChannelError` on
  2026-07-28. This repo's enhancers gate on `client_can_elicit()` and take
  their non-elicitation fallback there.
- **Logging** (`notifications/message`) - *deprecated* (SEP-2577); log level is
  now opted into per request via `_meta` (`logging/setLevel` removed).

## 4. Extensions and MCP Apps

**Extensions** are the 2026-07-28 framework for optional features outside the
core (SEP-2133): both sides advertise them under `capabilities.extensions`,
keyed by identifier (e.g. `io.modelcontextprotocol/tasks`). An extension MUST
degrade gracefully for peers that didn't negotiate it.

**MCP Apps** is the `io.modelcontextprotocol/ui` extension
([ext-apps](https://github.com/modelcontextprotocol/ext-apps)):
interactive iframe-sandboxed UIs (HTML resources via `ui://` URIs) embedded
in chat clients, with bidirectional `postMessage` / JSON-RPC communication.
*Not* a synonym for "MCP application" - Claude Desktop is a Host, your
backend is a Server, neither is an "App".
([OpenAI Apps SDK](https://developers.openai.com/apps-sdk/concepts/mcp-server))
This server advertises it (`extensions=[Apps()]`) but keeps emitting
`_meta.ui` on every revision, since handshake-era hosts predate extensions.

## 5. Transports

- **stdio** - Server runs **locally on the Host machine** as a child process
  spawned by the Client. No network. Use for IDE plugins, CLI tools,
  per-user local integrations.
- **Streamable HTTP** - Server runs **remotely**, reached over the network;
  one HTTP endpoint with optional SSE streaming, OAuth 2.1. Use for hosted/
  multi-tenant servers. On 2026-07-28: every POST carries `Mcp-Method` and
  `Mcp-Name` routing headers (SEP-2243); the GET stream and
  `resources/subscribe` are replaced by `subscriptions/listen`; SSE
  resumability (`Last-Event-ID`) is gone.
- **SSE** - *deprecated* legacy two-endpoint setup. Don't use for new servers.

## 6. Sessions, handshake and `server/discover`

**2026-07-28 is stateless** (SEP-2567, SEP-2575): no `initialize` handshake,
no `Mcp-Session-Id`, no `ping`. Every request carries in `params._meta`:

- `io.modelcontextprotocol/protocolVersion` (also the `MCP-Protocol-Version`
  header on HTTP),
- `io.modelcontextprotocol/clientCapabilities`,
- `io.modelcontextprotocol/clientInfo` (SHOULD).

Results carry `io.modelcontextprotocol/serverInfo` in `_meta`. Any replica can
serve any request. Cross-call state must be an explicit, server-minted handle
passed as an ordinary tool argument - never a "session". A version the server
doesn't speak gets `UnsupportedProtocolVersion` (`-32022`).

**`server/discover`** - a regular request (servers MUST implement it; clients
MAY call it) returning supported versions, capabilities, instructions and
identity. Replaces the handshake for up-front capability lookup.

**Handshake era (2025-11-25 and earlier)** still works against this server:

1. Client → `initialize` (protocolVersion, capabilities, clientInfo).
2. Server → response with its protocolVersion + capabilities.
3. Client → `notifications/initialized`.

Then the negotiated version rides the `MCP-Protocol-Version` header. The SDK
routes by that header: handshake versions go to the legacy transport (which
`server.mcp_stateless_http` configures), anything else to the 2026-07-28
entry. **Capabilities** are feature flags either way - don't call methods
whose capability wasn't advertised (`-32021` MissingRequiredClientCapability).

## 7. OAuth 2.1 pitfalls

- MCP server is a **resource server**, not the authorization server (token factory).
  DaySurface implements exactly that split: RFC 9728 Protected Resource
  Metadata at `/.well-known/oauth-protected-resource[/mcp]`
  (`api_server/routes/well_known.py`), a `resource_metadata` hint in the 401
  `WWW-Authenticate` challenge (`api_server/middleware/mcp_auth.py`), and
  audience-bound token validation against WorkOS AuthKit
  (`api_server/auth/authkit_auth.py`). AuthKit is the AS and handles
  CIMD/DCR/PKCE. Enable via `WORKOS_AUTHKIT_DOMAIN` + `MCP_PUBLIC_URL`.
- Sessions are anonymous unless wired through OAuth. Machine identity is
  now first-class (M2M client_credentials, Cross App Access).
- `resource` parameter (RFC 8707) must match the AS's expected URL
  **exactly** (trailing slash matters) - mismatches cause silent 401s.
- Use **CIMD** (Client ID Metadata Documents) for client registration;
  Dynamic Client Registration (DCR) is **deprecated** in 2026-07-28 (still
  available for older authorization servers).
- 2026-07-28 also: AS SHOULD send `iss` in authorization responses and
  clients MUST validate it (RFC 9207, SEP-2468); client credentials are bound
  to the issuing AS (SEP-2352); DCR clients set `application_type` (SEP-837).
- 🟡 **Scopes:** prefer least privilege - ask progressively, not upfront.
- 🟡 **Token storage:** persist tokens so users don't re-consent on each
  Host restart. Once SEP-1932 (DPoP) lands, you persist the *key*, not the
  bearer token.

## Sources

- [MCP Specification (2026-07-28)](https://modelcontextprotocol.io/specification/2026-07-28)
- [MCP Changelog (2026-07-28)](https://modelcontextprotocol.io/specification/2026-07-28/changelog)
- [The 2026-07-28 Specification (release post)](https://blog.modelcontextprotocol.io/posts/2026-07-28/)
- [Python SDK 2.x - what's new](https://py.sdk.modelcontextprotocol.io/whats-new/)
- [Python SDK 2.x - migration guide](https://py.sdk.modelcontextprotocol.io/migration/)
- [2026 MCP Roadmap](https://blog.modelcontextprotocol.io/posts/2026-mcp-roadmap/)
- [MCP Architecture](https://modelcontextprotocol.io/docs/learn/architecture)
- [MCP Transports](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports)
- [MCP Authorization](https://modelcontextprotocol.io/docs/tutorials/security/authorization)
- [WorkOS - Tools, Resources, Prompts, Sampling, Roots, Elicitation](https://workos.com/blog/mcp-features-guide)
- [MCPcat - Server vs Client vs Host](https://mcpcat.io/blog/mcp-server-client-host/)
- [fka.dev - Why MCP deprecated SSE](https://blog.fka.dev/blog/2025-06-06-why-mcp-deprecated-sse-and-go-with-streamable-http/)
- [Obsidian Security - OAuth pitfalls in MCP](https://www.obsidiansecurity.com/blog/when-mcp-meets-oauth-common-pitfalls-leading-to-one-click-account-takeover)
- [Auth0 - CIMD vs DCR for MCP](https://auth0.com/blog/cimd-vs-dcr-mcp-registration/)
