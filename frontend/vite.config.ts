import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The backend API is not built yet. When it is, add a dev proxy here
// (server.proxy) instead of hard-coding URLs in the client.
export default defineConfig({
  plugins: [react()],
});
