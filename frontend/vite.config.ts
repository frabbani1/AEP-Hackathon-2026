import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

// The client calls same-origin /api (see src/api/httpClient.ts). In development this proxies it to the local API
// (`scripts/dev_api.py`, port 8787). Point VITE_API_PROXY at another API to use that one instead. The API has no
// authentication, so only proxy to a target you trust.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "VITE_");
  return {
    plugins: [react()],
    server: {
      proxy: {
        "/api": { target: env.VITE_API_PROXY || "http://127.0.0.1:8787", changeOrigin: true },
      },
    },
  };
});
