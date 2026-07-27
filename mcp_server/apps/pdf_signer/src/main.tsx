import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App as McpApp } from "@modelcontextprotocol/ext-apps";
import { Signer } from "./Signer";
import { bufferToolResults } from "./helpers";

const mcpApp = new McpApp({ name: "pdf-signer", version: "0.1.0" });

// Install the ontoolresult buffer before connect()/render so a result the
// host delivers during connection is replayed into the Signer on mount.
const resultBuffer = bufferToolResults(mcpApp);

mcpApp
  .connect()
  .then(() => {
    createRoot(document.getElementById("root")!).render(
      <StrictMode>
        <Signer mcpApp={mcpApp} resultBuffer={resultBuffer} />
      </StrictMode>
    );
  })
  .catch((err) => {
    const root = document.getElementById("root");
    if (root) root.textContent = `Connection failed: ${err}`;
  });
