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

// 字体资源所在的唯一源集合（preconnect 按「协议+域名+端口」生效，一条覆盖
// 同源的全部 CSS 与 woff2）。从 CDN_FONT_LINKS 派生而非写死：换 CDN 只需
// 改上面的数组，预热连接自动跟随，不会留下指向旧域名的无效配置。
const CDN_FONT_ORIGINS = [
  ...new Set(CDN_FONT_LINKS.map((href) => new URL(href).origin)),
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
            // bundle-preload：先预热字体源连接（DNS + TLS），缩短阻塞
            // 渲染的字体样式表建立连接的时间；源从 CDN_FONT_LINKS 派生
            ...CDN_FONT_ORIGINS.map((origin) => ({
              tag: "link",
              attrs: {
                rel: "preconnect",
                href: origin,
                crossorigin: true,
              },
              injectTo: "head-prepend",
            })),
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
              // @base-ui / lucide-react 不做 manualChunks 强制分组，也不
              // 落入末尾兜底 vendor chunk（bundle-dynamic-imports）：入口
              // 只静态引用其中一小部分，任何整库分组都会把懒加载路由引用
              // 的全部组件/图标聚进首屏。返回 undefined 交给 Rollup 默认
              // 算法跟随消费者分包（组件与图标均已子路径深导入，天然可拆）
              if (/@base-ui|@radix-ui|lucide-react/.test(id)) return;
              if (
                /\/node_modules\/react(-dom)?\//.test(id) ||
                /\/node_modules\/scheduler\//.test(id)
              )
                return "vendor-react";
              if (/react-router|@remix-run\/router/.test(id))
                return "vendor-router";
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
              // pdfjs-dist（PDF 预览渲染层，editor v3 阶段一）：仅经动态
              // import() 首次预览才加载，独立分包防止落入首屏 vendor chunk
              // （worker 资产经 ?url 引入为独立 asset，不经分包）
              if (/pdfjs-dist/.test(id)) return "vendor-pdfjs";
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
