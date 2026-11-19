import { defineConfig } from "vitest/config";

// Plain Node runner: the Worker code uses only web-standard APIs (fetch types, Web Crypto,
// streams), and handle() takes its fetch and clock as arguments, so no workerd is needed.
export default defineConfig({
  test: {
    include: ["test/**/*.test.ts"],
    environment: "node",
  },
});
