---
name: daysurface
description: Connect a Gmail account to an MCP client via DaySurface - rank and triage the inbox, archive threads, and draft replies the user edits and sends.
---

# DaySurface

## What DaySurface does

DaySurface connects your Gmail account to the AI client you already use. Ask
what needs your attention and get a ranked inbox. Archive threads and mark them
done. Draft a reply in a real composer, edit it yourself, and send it - inside
Claude, ChatGPT, or any MCP client.

To do this, DaySurface asks permission to read, organise, draft, and send mail
in your Gmail account. It never permanently deletes mail. Message content is
fetched only when you ask for it and passed to your AI client; we do not store
message bodies, and no AI model on our servers reads your mail. See our privacy
policy: https://daysurface.com/privacy

## How it works

DaySurface is a Model Context Protocol (MCP) server. Use it to rank, triage, and
draft replies for a Gmail inbox. The same tools are also reachable over a CLI
and a plain HTTP API; behaviour is identical across all three transports.

## Connect

Add the MCP server to your client, then discover its tools.

- Endpoint (streamable HTTP): `https://mcp.daysurface.com/mcp`
- Server name: `daysurface`
- Discovery metadata: `https://daysurface.com/.well-known/mcp.json`

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

- Full description for LLMs: `https://daysurface.com/llms-full.txt`
- Human documentation: `https://daysurface.com/docs`
- Source code: `https://github.com/Miyamura80/DaySurface`
