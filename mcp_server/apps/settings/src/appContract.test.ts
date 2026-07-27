// Guards against drift between our McpAppLike structural type (what the
// component test mocks) and the real @modelcontextprotocol/ext-apps App class
// (what main.tsx passes in production).
import { describe, expect, it } from "vitest";
import { App } from "@modelcontextprotocol/ext-apps";
import type { McpAppLike } from "./Settings";

describe("McpAppLike contract vs real ext-apps App", () => {
  const app = new App({ name: "contract-test", version: "0.0.0" });

  it("real App is assignable to McpAppLike (compile-time)", () => {
    const asLike: McpAppLike = app;
    expect(asLike).toBe(app);
  });

  it("exposes callServerTool as a method", () => {
    expect(typeof app.callServerTool).toBe("function");
  });

  it("ontoolresult is a settable callback property", () => {
    const cb = () => undefined;
    app.ontoolresult = cb;
    expect(app.ontoolresult).toBe(cb);
  });
});
