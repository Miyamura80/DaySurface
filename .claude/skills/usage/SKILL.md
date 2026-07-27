---
name: usage
description: How to use the CLI, API, and MCP interfaces. Use this skill when interacting with the tool as an end user.
---
# Usage Guide

This skill teaches you how to use the three interfaces provided by this project.

## CLI

```bash
# Install
pip install daysurface

# Basic usage
daysurface --help                  # see all commands
daysurface greet Alice             # run a command
daysurface config show             # view configuration
daysurface doctor                  # check system health

# Global flags (go before the subcommand)
daysurface --verbose greet Alice   # detailed output
daysurface --format json config show  # JSON output
daysurface --dry-run greet Bob     # preview without executing
daysurface --version               # print version
```

## Server (HTTP API + MCP)

```bash
# Start the server: HTTP API and MCP (streamable HTTP) on one port.
daysurface-serve

# Default http://localhost:8080. See /docs for OpenAPI, /mcp for MCP.
```

## MCP

The MCP server exposes the same services as CLI tools via the Model Context Protocol.

**Primary transport: streamable HTTP at `/mcp`** (started by `daysurface-serve`).
Stdio is supported via `daysurface-mcp` for local Claude Desktop / dev only.

```bash
# Legacy stdio transport
daysurface-mcp

# Debug with the MCP inspector (stdio)
mcp dev mcp_server/server.py
```

### Connecting MCP to your editor

Remote (preferred - works on Claude Desktop 0.7+, Cursor, etc.):

```json
{
  "mcpServers": {
    "daysurface": {
      "url": "https://YOUR-DEPLOYMENT/mcp",
      "headers": { "X-API-KEY": "sk_..." }
    }
  }
}
```

Local stdio (legacy):

```json
{
  "mcpServers": {
    "daysurface": {
      "command": "daysurface-mcp"
    }
  }
}
```

## Updating

```bash
daysurface update    # check for updates and upgrade
```
