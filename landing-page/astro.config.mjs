// @ts-check
import { defineConfig } from "astro/config";
import tailwindcss from "@tailwindcss/vite";

// Static landing page. Output is plain HTML/CSS in `dist/`, served in
// production by `sirv` (see package.json `start` + railway.toml).
// https://astro.build/config
export default defineConfig({
  // Set this to the deployed origin so canonical/OG URLs resolve correctly.
  site: "https://gmailmcp.com",
  output: "static",
  vite: {
    plugins: [tailwindcss()],
  },
});
