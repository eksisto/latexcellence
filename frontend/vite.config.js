import { defineConfig, loadEnv } from "vite";
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
        : [
            // bundle-preload：字体 CSS 均来自该 CDN，先预热连接
            // （DNS + TLS），缩短阻塞渲染的字体样式表建立连接的时间
            {
              tag: "link",
              attrs: {
                rel: "preconnect",
                href: "https://s4.zstatic.net",
                crossorigin: true,
              },
              injectTo: "head-prepend",
            },
            ...CDN_FONT_LINKS.map((href) => ({
              tag: "link",
              attrs: { rel: "stylesheet", href },
              injectTo: "head-prepend",
            })),
          ];
      return { html, tags };
    },
  };
}

// 默认后端地址可由维护者通过 .env 的 VITE_API_BASE_URL 配置（见 .env.example）；
// dev 代理目标从该地址派生（去掉末尾 /api），便于 npm run dev 直连远程后端。
function resolveDevProxyTarget(env) {
  const base = (env.VITE_API_BASE_URL || "").trim();
  if (!/^https?:\/\//i.test(base)) return "http://localhost:8000";
  const stripped = base.replace(/\/+$/, "").replace(/\/api$/i, "");
  return stripped || "http://localhost:8000";
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, __dirname, "");
  const devProxyTarget = resolveDevProxyTarget(env);

  return {
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
          target: devProxyTarget,
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
              // pinyin-pro（编辑器命令拼音匹配，阶段三）：仅经动态 import()
              // 按需加载，独立分包防止落入首屏 vendor chunk
              if (/pinyin-pro/.test(id)) return "vendor-pinyin";
              // opencc-js（命令中文名简→繁展示，commandZh.js）：仅 zh-TW
              // 界面经动态 import() 按需加载，独立分包防止 ~1MB 字典数据
              // 落入首屏 vendor chunk
              if (/opencc-js/.test(id)) return "vendor-opencc";
              // CodeMirror 6（编辑器 /editor 懒加载路由专用，避免拖累首屏；
              // 覆盖 @uiw 全系子包与叶子依赖，防止与 vendor 互相引用形成循环分包）
              if (
                /@codemirror|@lezer|@uiw|\/codemirror\/|style-mod|w3c-keyname|crelt|find-cluster-break/.test(
                  id,
                )
              )
                return "vendor-codemirror";
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
  };
});
