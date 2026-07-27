import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App as McpApp } from "@modelcontextprotocol/ext-apps";
import { Inbox } from "./Inbox";
import { bufferToolResults } from "./helpers";

const mcpApp = new McpApp({ name: "gmail-inbox", version: "0.1.0" });

// Install the ontoolresult buffer before connect()/render so a result the host
// delivers before <Inbox> mounts is captured, not lost (else a thread-open would
// fall back to a curated-inbox refresh instead of the requested thread reader).
const resultBuffer = bufferToolResults(mcpApp);

mcpApp
  .connect()
  .then(() => {
    createRoot(document.getElementById("root")!).render(
      <StrictMode>
        <Inbox mcpApp={mcpApp} resultBuffer={resultBuffer} />
      </StrictMode>
    );
  })
  .catch((err) => {
    const root = document.getElementById("root");
    if (root) root.textContent = `Connection failed: ${err}`;
  });
