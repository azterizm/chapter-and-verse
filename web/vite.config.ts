/// <reference types="vitest/config" />
import { fileURLToPath, URL } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";
import { mockApi } from "./mock/plugin.ts";

// The CSP the site must run under (README). `npm run preview` serves the production build with
// it, so a violation shows in the browser console before deploy.
function csp(apiBase: string): string {
  const api = apiBase ? ` ${new URL(apiBase).origin}` : "";
  return [
    `default-src 'self' https://challenges.cloudflare.com${api}`,
    "base-uri 'none'",
    "object-src 'none'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ].join("; ");
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  // The mock answers /api/* unless CV_MOCK=0 or the app points at another API origin.
  const useMock = env.CV_MOCK !== "0" && !env.VITE_API_BASE;
  const engineTarget = env.CV_ENGINE?.trim() || "http://127.0.0.1:8000";
  return {
    plugins: [tailwindcss(), react(), ...(useMock ? [mockApi()] : [])],
    resolve: {
      alias: {
        "@": fileURLToPath(new URL("./src", import.meta.url)),
      },
    },
    ...(!useMock && !env.VITE_API_BASE
      ? {
          server: {
            proxy: {
              "/api": {
                target: engineTarget,
                changeOrigin: true,
              },
            },
          },
        }
      : {}),
    build: {
      target: "es2022",
      modulePreload: { polyfill: false },
      sourcemap: true,
    },
    preview: {
      headers: {
        "Content-Security-Policy": csp(env.VITE_API_BASE ?? ""),
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
      },
    },
    test: {
      environment: "node",
      include: ["test/**/*.test.{ts,tsx}"],
    },
  };
});
