/**
 * One-click MCP install deep links, shared by ConnectWidget.astro (visible UI)
 * and WebMcp.astro (agent tools) so the URL formats can't drift between them.
 *
 * Runs at build time (Astro frontmatter), so Node's `Buffer` is available.
 *
 * Formats verified against official docs: cursor.com/docs/context/mcp/install-links,
 * microsoft/vscode-docs (api/extension-guides/ai/mcp.md), block/goose docs.
 *
 * Two shapes live here, and callers must not conflate them:
 * - PREFILLING links (Claude, Cursor, VS Code, Goose) carry the server name and
 *   URL, so the user only confirms.
 * - NAVIGATING links (ChatGPT) open the right settings screen and nothing more;
 *   the URL still has to be pasted by hand. `InstallTarget.prefills` is what
 *   keeps the UI and the agent-facing copy honest about which is which.
 */
export function deepLink(
  id: string,
  mcpUrl: string,
  serverName: string,
  displayName = serverName,
): string | null {
  switch (id) {
    case "claude": {
      // https://claude.ai/new?modal=add-custom-connector&connectorName=&connectorUrl=#settings/customize-connectors
      //
      // Click-tested: opens the "Add custom connector" dialog with both Name
      // and "Remote MCP server URL" already filled in. The user still presses
      // Add, and Claude shows a red "suggested by an external link" warning
      // above the fields - expected, and called out in the panel copy so it
      // doesn't read as a failure.
      //
      // Two things this format is picky about, both found the hard way:
      // the route is a HASH fragment while the flags are QUERY params, so the
      // query must come BEFORE the `#`; and the path is /new, not
      // /customize/connectors (that variant opens nothing).
      const params = new URLSearchParams({
        modal: "add-custom-connector",
        connectorName: displayName,
        connectorUrl: mcpUrl,
      });
      return `https://claude.ai/new?${params.toString()}#settings/customize-connectors`;
    }
    case "chatgpt": {
      // https://chatgpt.com/plugins
      //
      // A plain navigation link to ChatGPT's Plugins page, where custom MCP
      // servers are added via Add → Create custom MCP server. It prefills NOTHING,
      // hence `prefills: false` in connect.ts - do not relabel this "1-click".
      // ConnectWidget copies the server URL to the clipboard as it navigates,
      // so the remaining work is two clicks and a paste.
      //
      // History: until mid-2026 custom connectors lived under Settings →
      // Connectors behind a personal "Developer mode" toggle, and this link was
      // `/plugins#settings/Connectors?create-connector=true&redirectAfter=%2Fplugins`,
      // which opened the create dialog directly. OpenAI then renamed
      // Connectors to Apps, moved them into the Plugins directory and removed
      // the personal Developer mode toggle (business workspaces still gate it
      // behind an admin permission), so that hash route targets a flow that no
      // longer exists. Verified October 2026.
      //
      // Prefill params were click-tested July 2026 and none exist. Tried, both
      // inside the hash and ahead of it: name/url, connectorName/connectorUrl
      // (Claude's spelling), connector-name/connector-url, server_label/
      // server_url (OpenAI's own Responses API MCP naming), serverUrl, mcp_url,
      // mcpUrl, description. Every one was ignored - the dialog still opened
      // blank. Don't re-run this matrix without new evidence; the absence of
      // any "Add to ChatGPT" badge across the MCP ecosystem is the corroborating
      // signal that OpenAI ships no prefill surface at all.
      return "https://chatgpt.com/plugins";
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
