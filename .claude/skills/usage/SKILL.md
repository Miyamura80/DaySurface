---
name: usage
description: How to use the CLI, API, and MCP interfaces. Use this skill when interacting with the tool as an end user.
---
# Usage Guide

This skill teaches you how to use the three interfaces provided by this project.

## CLI

```bash
# Install
pip install mcp-template

# Basic usage
mymcp --help                  # see all commands
mymcp greet Alice             # run a command
mymcp config show             # view configuration
mymcp doctor                  # check system health

# Global flags (go before the subcommand)
mymcp --verbose greet Alice   # detailed output
mymcp --format json config show  # JSON output
mymcp --dry-run greet Bob     # preview without executing
mymcp --version               # print version
```

## Server (HTTP API + MCP)

```bash
# Start the server: HTTP API and MCP (streamable HTTP) on one port.
mymcp-serve

# Default http://localhost:8080. See /docs for OpenAPI, /mcp for MCP.
```

## MCP

The MCP server exposes the same services as CLI tools via the Model Context Protocol.

**Primary transport: streamable HTTP at `/mcp`** (started by `mymcp-serve`).
Stdio is supported via `mymcp-mcp` for local Claude Desktop / dev only.

```bash
# Legacy stdio transport
mymcp-mcp

# Debug with the MCP inspector (stdio)
mcp dev mcp_server/server.py
```

### Connecting MCP to your editor

Remote (preferred - works on Claude Desktop 0.7+, Cursor, etc.):

```json
{
  "mcpServers": {
    "mymcp": {
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
    "mymcp": {
      "command": "mymcp-mcp"
    }
  }
}
```

## Updating

```bash
mymcp update    # check for updates and upgrade
```
