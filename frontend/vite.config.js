import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "path";

// 生产构建通过 cdnjs 加载字体，避免把大体积的 woff2 打包进 static/。
// 版本号固定，避免 cdnjs 包更新导致引用漂移；本地版本可在 npm 上略新（如文楷 1.522.0）。
const CDN_FONT_LINKS = [
  "https://s4.zstatic.net/ajax/libs/lxgw-wenkai-web/1.520.0/lxgwwenkai-regular/result.css",
  "https://s4.zstatic.net/ajax/libs/lxgw-wenkai-web/1.520.0/lxgwwenkaimono-regular/result.css",
  "https://s4.zstatic.net/ajax/libs/fontsource-noto-sans-sc/5.2.8/500.css",
  "https://s4.zstatic.net/ajax/libs/fontsource-jetbrains-mono/5.2.8/400.css",
  "https://s4.zstatic.net/ajax/libs/fontsource-stix-two-text/5.2.8/latin-400.css",
  "https://s4.zstatic.net/ajax/libs/fontsource-stix-two-text/5.2.8/latin-500.css",
  "https://s4.zstatic.net/ajax/libs/fontsource-stix-two-text/5.2.8/latin-600.css",
];

function fontSourcePlugin() {
  return {
    name: "font-source",
    transformIndexHtml(html, ctx) {
      const tags = ctx.server
        ? [
            {
              tag: "link",
              attrs: {
                rel: "stylesheet",
                href: "/src/fonts/index.css",
              },
              injectTo: "head-prepend",
            },
          ]
        : CDN_FONT_LINKS.map((href) => ({
            tag: "link",
            attrs: { rel: "stylesheet", href },
            injectTo: "head-prepend",
          }));
      return { html, tags };
    },
  };
}

export default defineConfig({
  plugins: [fontSourcePlugin(), react(), tailwindcss()],
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
            if (
              /\/node_modules\/react(-dom)?\//.test(id) ||
              /\/node_modules\/scheduler\//.test(id)
            )
              return "vendor-react";
            if (/@base-ui|@radix-ui/.test(id)) return "vendor-ui";
            if (/react-router|@remix-run\/router/.test(id))
              return "vendor-router";
            if (/lucide-react/.test(id)) return "vendor-icons";
            if (/sonner/.test(id)) return "vendor-sonner";
            if (/katex/.test(id)) return "vendor-katex";
            // 以下库只被懒加载路由使用（MarkdownRenderer / CodeBlock 等），
            // 独立分包，避免随 i18next/zustand 等首屏依赖一起进入初始 vendor chunk。
            if (/highlight\.js/.test(id)) return "vendor-highlight";
            if (
              /react-markdown|remark-|rehype-|unified|hast-util-|mdast-util-|unist-util-|micromark|vfile|property-information|character-entities|comma-separated-tokens|space-separated-tokens|decode-named-character-reference|escape-string-regexp|is-plain-obj|trough|bail|extend|trim-lines|ccount|zwitch|devlop|dequal|markdown-table|longest-streak|web-namespaces|html-void-elements|stringify-entities|parse-entities|hastscript|style-to-js|style-to-object|inline-style-parser/.test(
                id,
              )
            )
              return "vendor-markdown";
            return "vendor";
          }
        },
      },
    },
  },
});
