/**
 * KaTeX 公式悬停预览（editor-productivity-plan §5.3 阶段六 / 决策 Q9）。
 *
 * CM6 hoverTooltip 扩展：指针在公式区域停留约 500ms（Q9 决策）后弹出
 * 浮层，katex.renderToString 渲染公式全文（含定界符；env 直接整段喂给
 * KaTeX，它原生支持 align / equation 系环境）。
 *
 * 关键设计：
 *   - katex 动态分块 import（决策 Q9）：`import("katex")` 首次悬停才加载，
 *     首屏零 katex JS；katex.min.css 同样动态 import（vite 构建为独立
 *     CSS 资产，与 JS 块同拍按需注入）——编辑器路由不静态引 katex CSS，
 *     不阻塞首屏。模块级缓存 Promise，全程只加载一次；react-markdown
 *     渲染链（MarkdownRenderer，懒加载组件）是另一条独立通路，互不影响。
 *   - 公式判定委托 formulaHoverScan.findFormulaAt（单向单遍状态机，
 *     500ms 悬停延迟 + 按位置前缀扫描，见该模块头注）。
 *   - 渲染失败不抛未捕获异常：KaTeX parseError → 浮层显示 i18n 错误
 *     提示（translate 注入，components:editor.formulaHover.* 键）；
 *     katex 块加载失败（网络异常）同样落到错误提示。
 *   - XSS：renderToString 固定 `trust: false`（禁止 \href 等写入任意
 *     属性）+ `output: "html"`（默认）+ `strict: "ignore"`（容忍非标准
 *     LaTeX，不产生控制台告警），浮层内不放原始用户 HTML。
 *   - 换档兼容：tooltip 属于 EditorState 的 tooltip 状态，tab 切换
 *     （view.setState）与文档变更（hover tooltip 默认 hideOnChange）时
 *     由 CM6 自动收起，无须额外处理；扩展恒挂载，开关经 isEnabled 实时
 *     读取（同 autoCloseEnv 的 ref 注入模式，偏好切换不重配置扩展）。
 *   - 主题：EditorView.theme 走全站 CSS 变量（--card / --border /
 *     --surface-*，与 completionTheme.js 同源浮层词汇），亮/暗主题
 *     切换零 JS 参与；`.cm-tooltip` 前缀压过 CM base theme 默认规则。
 */

import { EditorView, hoverTooltip } from "@codemirror/view";

import { findFormulaAt } from "./formulaHoverScan";

/** 悬停触发延迟（Q9：约 500ms） */
const HOVER_TIME_MS = 500;

/** katex 渲染参数（trust:false 为 XSS 约束，禁止覆盖） */
const KATEX_RENDER_OPTIONS = Object.freeze({
  displayMode: false, // 由每个公式的 region.display 覆盖
  throwOnError: true, // parseError 抛出 → 统一落错误提示，不出 KaTeX 红字
  strict: "ignore",
  trust: false,
  output: "html",
});

/** katex 模块 + CSS 的按需单例加载（失败不缓存，下次悬停可重试） */
let katexPromise = null;
function loadKatex() {
  if (!katexPromise) {
    katexPromise = Promise.all([
      import("katex"),
      import("katex/dist/katex.min.css"),
    ]).then(([katex]) => katex.default ?? katex);
  }
  return katexPromise;
}

/**
 * 把公式渲染进浮层 DOM（异步：等待 katex 块加载）。所有失败路径都
 * 收敛为错误提示文本，绝不向调用方（CM6 事件循环）抛异常。
 */
async function renderFormulaInto(dom, formula, translate) {
  let katex;
  try {
    katex = await loadKatex();
  } catch (_) {
    dom.classList.add("cm-formula-hover-error");
    dom.textContent = translate("components:editor.formulaHover.renderError");
    return;
  }
  try {
    dom.innerHTML = katex.renderToString(formula.text, {
      ...KATEX_RENDER_OPTIONS,
      displayMode: formula.display,
    });
  } catch (_) {
    // KaTeX parseError（非法 LaTeX）：展示错误提示而非空白 / 崩溃
    dom.classList.add("cm-formula-hover-error");
    dom.textContent = translate("components:editor.formulaHover.renderError");
  }
}

/** 浮层主题：与补全弹层同源的卡片外观（CSS 变量随亮/暗主题切换） */
export const formulaHoverTheme = EditorView.theme({
  ".cm-tooltip.cm-formula-hover-tooltip": {
    border: "1px solid var(--border)",
    borderRadius: "var(--surface-radius)",
    backgroundColor: "var(--card)",
    boxShadow: "var(--surface-shadow)",
    padding: "8px 12px",
    maxWidth: "min(480px, 92vw)",
    overflow: "hidden",
  },
  ".cm-tooltip.cm-formula-hover-tooltip .cm-formula-hover": {
    fontFamily:
      "var(--font-mono), ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
    fontSize: "15px",
    color: "var(--foreground)",
    overflowX: "auto",
  },
  // display 公式去掉 katex 默认的大外边距，避免浮层抖动
  ".cm-tooltip.cm-formula-hover-tooltip .katex-display": {
    margin: "0",
  },
  ".cm-tooltip.cm-formula-hover-tooltip .cm-formula-hover-error": {
    fontFamily: "var(--font-sans)",
    fontSize: "12px",
    color: "var(--destructive)",
  },
});

/**
 * 公式悬停预览扩展（主题 + hoverTooltip）。
 * @param {{isEnabled?: () => boolean, translate?: (key: string) => string}} [options]
 *   isEnabled：formulaHover 偏好读取器（每次悬停求值，缺省恒开）；
 *   translate：i18n 翻译函数（接收 components:editor.formulaHover.* 完整键）。
 * @returns {import("@codemirror/view").Extension}
 */
export function formulaHoverExtension({
  isEnabled = () => true,
  translate = (key) => key,
} = {}) {
  return [
    formulaHoverTheme,
    hoverTooltip(
      (view, pos) => {
        if (!isEnabled()) return null;
        const formula = findFormulaAt(view.state.doc.toString(), pos);
        if (!formula) return null;
        return {
          pos: formula.from,
          class: "cm-formula-hover-tooltip",
          create() {
            const dom = document.createElement("div");
            dom.className = "cm-formula-hover";
            void renderFormulaInto(dom, formula, translate);
            return { dom };
          },
        };
      },
      { hoverTime: HOVER_TIME_MS },
    ),
  ];
}
