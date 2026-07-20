import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "path";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "../static",
    emptyOutDir: true,
    rollupOptions: {
      output: {
        assetFileNames: (assetInfo) => {
          const name = assetInfo.name || "";
          if (/\.(woff2?|ttf|otf|eot)$/i.test(name)) {
            return "assets/fonts/[name]-[hash][extname]";
          }
          return "assets/[name]-[hash][extname]";
        },
        manualChunks(id) {
          if (id.includes("node_modules")) {
            if (/react-dom|\/react\/|scheduler\//.test(id)) return "vendor-react";
            if (/react-router|@remix-run\/router/.test(id)) return "vendor-router";
            if (/@base-ui|@radix-ui/.test(id)) return "vendor-ui";
            if (/lucide-react/.test(id)) return "vendor-icons";
            if (/sonner/.test(id)) return "vendor-sonner";
            if (/lxgwwenkai-regular/.test(id)) return "font-wenkai";
            if (/lxgwwenkaimono-regular/.test(id)) return "font-wenkai-mono";
            if (/noto-sans-sc/.test(id)) return "font-noto-sans-sc";
            if (/jetbrains-mono/.test(id)) return "font-jetbrains-mono";
            return "vendor";
          }
        },
      },
    },
  },
});
