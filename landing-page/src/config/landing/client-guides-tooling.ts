/**
 * Guides for the MCP Apps hosts that are not everyday chat assistants: the two
 * inspection tools and the one enterprise assistant.
 *
 * These are here rather than in `client-guides.ts` because they are written for
 * a different reader. Postman and MCPJam get `DEV_CAPABILITIES` - someone
 * connecting there is evaluating the server, not clearing their inbox. Microsoft
 * 365 Copilot gets the end-user list but an admin's setup path, because nobody
 * adds a connector to a Microsoft tenant from a settings screen of their own.
 *
 * Every step below is taken from the vendor's own documentation, not inferred.
 * Two Apps hosts from the matrix are deliberately absent for exactly that
 * reason:
 *
 *   - Archestra.AI - an enterprise gateway and registry. The documented flow is
 *     installing servers from its MCP Registry; no primary source found for
 *     adding an arbitrary remote streamable-HTTP server, so the click-path would
 *     be invention.
 *   - PostHog Code - no client-side documentation found for adding a
 *     third-party MCP server at all. Every result describes PostHog's own MCP
 *     *server*, which is the opposite direction.
 *
 * If either publishes a setup path, they belong in this file. Do not guess one.
 */
import {
  type ClientGuide,
  CAPABILITIES,
  DEV_CAPABILITIES,
  SHARED_FAQ,
} from "./client-guides-shared";

export const toolingGuides: ClientGuide[] = [
  {
    slug: "postman",
    targetId: "postman",
    clientName: "Postman",
    title: "Connect Gmail to Postman - inspect the DaySurface MCP server",
    description:
      "Open the DaySurface Gmail MCP server as an MCP request in Postman: list its tools, call them by hand and watch the OAuth exchange before you add it to a client you use daily.",
    heading: "Connect Gmail to Postman",
    subhead: "For looking at the server, not for reading your mail.",
    lede: "Postman speaks MCP as a client, so you can point it at DaySurface and see the whole tool surface before committing to it. This is the page for evaluating the server rather than using it - if you want your inbox in an assistant, the Claude and ChatGPT guides are the ones you want. Postman does render MCP Apps, so the dashboard resources mount here too.",
    prerequisites: [
      "Postman, desktop or web.",
      "A Google account with Gmail. Even a read-only look at the tool list needs the OAuth sign-in, because the tools are scoped to a mailbox.",
    ],
    steps: [
      {
        title: "Create an MCP request",
        body: "Make a new request, then click the icon next to the Untitled Request label and pick MCP. This is a distinct request type from HTTP or GraphQL, and it will not appear if you start from a plain HTTP request.",
      },
      {
        title: "Switch the transport to HTTP",
        body: "In the input below the request label, change the dropdown from STDIO to HTTP. STDIO is for servers that run as a local process - DaySurface is remote, so that mode has nothing to launch.",
      },
      {
        title: "Paste the endpoint and connect",
        body: "Paste the DaySurface endpoint and click Connect. Postman runs the handshake and lists the tools the server exposes, with their schemas.",
      },
      {
        title: "Sign in with Google",
        body: "Complete the OAuth flow when Postman opens it. Review the scopes before approving - this grants real access to a real mailbox, which is worth remembering when the context is a testing tool.",
      },
      {
        title: "Call a tool",
        body: "Pick a read-only tool and run it with your own arguments to confirm the round trip. Use Disconnect and Connect to re-run the handshake if you change anything server-side.",
      },
    ],
    capabilities: DEV_CAPABILITIES,
    faq: [
      {
        q: "Is this how I should use DaySurface day to day?",
        a: "No. Postman is for inspecting the server. For actually working your inbox you want a chat client - Claude, ChatGPT, VS Code, Cursor or Goose all have their own guide.",
      },
      {
        q: "STDIO or HTTP?",
        a: "HTTP. DaySurface is a remote server over streamable HTTP, so there is no local process for the STDIO transport to spawn.",
      },
      ...SHARED_FAQ,
    ],
  },
  {
    slug: "mcpjam",
    targetId: "mcpjam",
    clientName: "MCPJam",
    title: "Connect Gmail to MCPJam - test the DaySurface MCP server",
    description:
      "Load the DaySurface Gmail MCP server in the MCPJam inspector: browse tools, resources and prompts, exercise the full OAuth flow, and read every JSON-RPC message on the wire.",
    heading: "Connect Gmail to MCPJam",
    subhead: "The inspector view: every tool, every message, the whole auth exchange.",
    lede: "MCPJam is an inspector for MCP servers, and it is the tool to reach for when something is not working and you want to see why. It shows tools, resources and prompts, implements the MCP authorisation spec including dynamic client registration, and renders MCP Apps - so the ui:// surfaces mount rather than showing as raw payloads.",
    prerequisites: [
      "MCPJam.",
      "A Google account with Gmail, since the tools are scoped to a mailbox.",
    ],
    steps: [
      {
        title: "Choose the HTTP transport",
        body: "In the transport dropdown, select HTTP. MCPJam also speaks STDIO for local servers, which is the wrong mode for a remote endpoint.",
      },
      {
        title: "Paste the endpoint",
        body: "Enter the DaySurface endpoint - it ends in /mcp, which is the usual shape for a streamable-HTTP server.",
      },
      {
        title: "Pick OAuth",
        body: "Choose OAuth rather than Bearer Token. DaySurface uses dynamic client registration, so there is no token to obtain in advance and nothing to paste; MCPJam runs the registration and the authorisation flow for you.",
      },
      {
        title: "Connect and browse",
        body: "Connect, then complete the Google sign-in. The tools, resources and prompts the server exposes are listed once the handshake finishes.",
      },
      {
        title: "Read the trace when something breaks",
        body: "MCPJam shows the JSON-RPC messages and the OAuth exchange. If a connection fails, the trace usually names the reason faster than any log on the server side.",
      },
    ],
    capabilities: DEV_CAPABILITIES,
    faq: [
      {
        q: "OAuth or bearer token?",
        a: "OAuth. Bearer token is for servers where you already hold a long-lived credential. DaySurface issues one through dynamic client registration during the flow, so there is nothing to paste up front.",
      },
      {
        q: "Can I use this to debug my own deployment?",
        a: "Yes, and it is the main reason to reach for it. Point it at your self-hosted instance instead of the hosted endpoint and the same inspection applies.",
      },
      ...SHARED_FAQ,
    ],
  },
  {
    slug: "microsoft-365-copilot",
    // Null on purpose - see the field's doc comment. The install picker cannot
    // offer a tenant-admin provisioning flow, so this guide is the only place
    // M365 Copilot is documented.
    targetId: null,
    clientName: "Microsoft 365 Copilot",
    title: "Connect Gmail to Microsoft 365 Copilot - DaySurface",
    description:
      "Add the DaySurface Gmail MCP server to Microsoft 365 Copilot as a custom connector, or as an MCP tool on a Copilot Studio agent. Requires a tenant administrator.",
    heading: "Connect Gmail to Microsoft 365 Copilot",
    subhead: "An admin adds this one - there is no per-user settings screen.",
    lede: "Microsoft 365 Copilot reaches third-party MCP servers through connectors managed at the tenant level, so this is not something an individual turns on for themselves. There are two documented routes: a custom connector in the Microsoft 365 admin center, which makes the server available across the tenant, or an MCP tool on a specific Copilot Studio agent. Both need elevated rights.",
    prerequisites: [
      "A Google account with Gmail.",
      "Global Administrator or AI Administrator in the Microsoft 365 admin center for the connector route.",
      "For the admin-center route, authentication configured in advance - the connector form asks for a registration ID, not a URL and a password.",
    ],
    steps: [
      {
        title: "Open the Copilot connectors gallery",
        body: "Sign in to the Microsoft 365 admin center and go to Copilot, then Connectors. Open the Gallery tab.",
      },
      {
        title: "Start a custom connector",
        body: "Under Created by your org, find the Create a new connector tile and select Add.",
      },
      {
        title: "Point it at the MCP server",
        body: "On the custom connector page, under Connect to MCP server, select Add and supply the DaySurface endpoint along with the registration ID matching your authentication method - the SSO or OAuth registration ID from the Teams Developer Portal.",
      },
      {
        title: "Save and roll out",
        body: "Save to create the connector. It then follows your tenant's normal rollout and consent path rather than appearing instantly for everyone.",
      },
      {
        title: "Or add it to one Copilot Studio agent instead",
        body: "If you want it scoped to a single agent rather than the tenant, open that agent's Tools page in Copilot Studio, choose Add a tool, then New tool, then MCP, and run the onboarding wizard with the endpoint and OAuth 2.0.",
      },
    ],
    capabilities: CAPABILITIES,
    faq: [
      {
        q: "Can I add this myself without an admin?",
        a: "No. Both documented routes need elevated rights - Global or AI Administrator for a tenant connector, and maker access to the agent for the Copilot Studio route. If you want DaySurface on your own account today, use a client you control, such as Claude or ChatGPT.",
      },
      {
        q: "Microsoft's guidance mentions read-only tools. Does that rule this out?",
        a: "That guidance is about federated connectors used for grounding, where the tools are search and fetch. DaySurface exposes tools that change mailbox state as well, so check the route you pick against your own tenant policy before rolling it out - and note that sending is gated behind a draft you approve rather than happening on the model's judgement.",
      },
      ...SHARED_FAQ,
    ],
  },
];
