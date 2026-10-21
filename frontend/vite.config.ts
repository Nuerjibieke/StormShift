import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8080",
    },
  },
  build: {
    target: "es2022",
    sourcemap: true,
    chunkSizeWarningLimit: 1700,
    rollupOptions: {
      output: {
        manualChunks: {
          phaser: ["phaser"],
          react: ["react", "react-dom", "xstate", "@xstate/react"],
        },
      },
    },
  },
});
