import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App as McpApp } from "@modelcontextprotocol/ext-apps";
import { Settings } from "./Settings";

const mcpApp = new McpApp({ name: "settings", version: "0.1.0" });

mcpApp
  .connect()
  .then(() => {
    createRoot(document.getElementById("root")!).render(
      <StrictMode>
        <Settings mcpApp={mcpApp} />
      </StrictMode>
    );
  })
  .catch((err) => {
    const root = document.getElementById("root");
    if (root) root.textContent = `Connection failed: ${err}`;
  });
