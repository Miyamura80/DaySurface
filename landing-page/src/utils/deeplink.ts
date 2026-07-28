/**
 * One-click MCP install deep links, shared by ConnectWidget.astro (visible UI)
 * and WebMcp.astro (agent tools) so the URL formats can't drift between them.
 *
 * Runs at build time (Astro frontmatter), so Node's `Buffer` is available.
 *
 * Formats verified against official docs: cursor.com/docs/context/mcp/install-links,
 * microsoft/vscode-docs (api/extension-guides/ai/mcp.md), block/goose docs.
 * ChatGPT has no install URL scheme at all - it is paste-the-URL only, and
 * additionally requires Developer mode. Do not invent one.
 */
export function deepLink(id: string, mcpUrl: string, serverName: string): string | null {
  switch (id) {
    case "claude": {
      // https://claude.ai/new?modal=add-custom-connector#settings/customize-connectors
      //
      // Click-tested: lands on Connectors with the "Add custom connector"
      // dialog already open. It does NOT pre-fill - Name and "Remote MCP
      // server URL" both come up empty - so the copyable URL above the picker
      // is load-bearing here, not decorative, and the panel says to paste it.
      //
      // Note the shape: the modal flag is a QUERY param and the route is a
      // HASH fragment, so the query has to come before the `#`. An earlier
      // guess at /customize/connectors?connectorName=&connectorUrl= was wrong
      // on both the path and the prefill; don't reintroduce it without a
      // fresh click-test.
      return "https://claude.ai/new?modal=add-custom-connector#settings/customize-connectors";
    }
    case "cursor": {
      // cursor://anysphere.cursor-deeplink/mcp/install?name=&config=<base64({url})>
      const config = Buffer.from(JSON.stringify({ url: mcpUrl })).toString("base64");
      // base64 can contain +, /, = - encode so query parsers don't mangle it (e.g. + → space).
      return `cursor://anysphere.cursor-deeplink/mcp/install?name=${encodeURIComponent(serverName)}&config=${encodeURIComponent(config)}`;
    }
    case "vscode": {
      // https://vscode.dev/redirect/mcp/install?name=<name>&config=<uriComponent(JSON {type,url})>
      //
      // Preferred over the `vscode:mcp/install?<json>` scheme for a web CTA: an
      // https link is clickable from any browser, where a custom scheme is often
      // blocked or silently dropped. Same handler on the other end. Note the
      // shape differs between the two forms - here `name` is a query param and
      // is NOT part of the JSON. (Append &quality=insiders for Insiders.)
      const config = encodeURIComponent(JSON.stringify({ type: "http", url: mcpUrl }));
      return `https://vscode.dev/redirect/mcp/install?name=${encodeURIComponent(serverName)}&config=${config}`;
    }
    case "goose": {
      // goose://extension?url=&type=streamable_http&id=&name=&description=&timeout=
      const params = new URLSearchParams({
        url: mcpUrl,
        type: "streamable_http",
        id: serverName,
        name: serverName,
        description: `${serverName} MCP server`,
        timeout: "300",
      });
      return `goose://extension?${params.toString()}`;
    }
    default:
      return null;
  }
}
