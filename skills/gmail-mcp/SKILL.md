---
name: gmail-mcp
description: Triage and curate a Gmail inbox by connecting to the GmailMCP server over MCP and calling its typed tools.
---

# GmailMCP

GmailMCP is a Model Context Protocol (MCP) server. Use it to rank, triage, and
draft replies for a Gmail inbox. The same tools are also reachable over a CLI
and a plain HTTP API; behaviour is identical across all three transports.

## Connect

Add the MCP server to your client, then discover its tools.

- Endpoint (streamable HTTP): `https://mcp.gmailmcp.com/mcp`
- Server name: `gmail-mcp`
- Discovery metadata: `https://gmailmcp.com/.well-known/mcp.json`

## Use

1. List the available tools with the MCP `tools/list` method.
2. Call a tool with `tools/call` and typed JSON arguments. For example, to
   triage the inbox:

   ```json
   {
     "jsonrpc": "2.0",
     "id": 1,
     "method": "tools/call",
     "params": {
       "name": "gmail_curate_inbox",
       "arguments": { "limit": 3 }
     }
   }
   ```

3. The server returns structured output (e.g. threads ranked by an importance
   score, with a flag for whether a draft reply was prepared).

## Learn more

- Full description for LLMs: `https://gmailmcp.com/llms-full.txt`
- Human documentation: `https://gmailmcp.com/docs`
- Source code: `https://github.com/Miyamura80/MCP-Template`
