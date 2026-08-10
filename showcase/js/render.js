/* ============================================================
 * LateXcellence · Showcase — 页面渲染器
 * 依据 js/content.js（内容数据）与 js/themes.js（主题数据）
 * 生成右侧导轨、主题弹层选项与全部场景 DOM。
 * index.html 只保留静态骨架，本模块在 DOM 解析后执行。
 * ============================================================ */

import { NAV, SCENES } from "./content.js";
import { THEMES, themeGradient } from "./themes.js";
import { LAYOUT } from "./constants.js";

/* ---------- 小工具 ---------- */
function esc(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function escAttr(s) {
  return esc(s).replace(/"/g, "&quot;");
}

/* 数组 → 多行 <br> 文本；单值 → 纯文本 */
function lines(x) {
  return Array.isArray(x) ? x.map(esc).join("<br>") : esc(x || "");
}

function glyphsHtml(glyphs) {
  const grid = '<div class="grid-bg"></div>';
  if (!glyphs || !glyphs.length) {
    return `<div class="parallax-field" aria-hidden="true">${grid}</div>`;
  }
  const spans = glyphs
    .map(
      (g) =>
        `<span class="glyph ${g.cls}" data-parallax="${g.parallax}">${esc(g.text)}</span>`,
    )
    .join("\n");
  return `<div class="parallax-field" aria-hidden="true">${grid}${spans}</div>`;
}

function sceneOpen(s, extra) {
  const anchor = LAYOUT.RAIL_ANCHOR_P[s.tpl] ?? LAYOUT.RAIL_ANCHOR_RATIO;
  return (
    `<section class="scene${extra ? " " + extra : ""}" ` +
    `id="${s.id}" data-pin data-len="${s.len}" data-anchor="${anchor}" ` +
    `style="--len: ${s.len}" aria-label="${escAttr(s.label)}">`
  );
}

/* 按钮组：第一个为主按钮，其余为幽灵按钮（与原有页面一致） */
function actionHtml(a, i) {
  const variant = i === 0 ? "btn-primary" : "btn-ghost";
  const idAttr = a.id ? ` id="${a.id}"` : "";
  const icon = a.icon
    ? `<svg class="icon"><use href="#${a.icon}" /></svg>`
    : "";
  return `<a class="btn ${variant} btn-lg" href="${a.href}"${idAttr}>${esc(a.label)}${icon}</a>`;
}

/* ---------- 场景模板 ---------- */

/* 序章 · 封面 */
function renderHero(s) {
  const actions = s.actions.map(actionHtml).join("\n");
  const meta = s.meta.map((m) => `<span>${m}</span>`).join("\n");
  return `${sceneOpen(s, "scene-hero")}
        <div class="pin">
          ${glyphsHtml(s.glyphs)}
          <div class="scene-inner hero-inner">
            <div class="hero-copy">
              <p class="eyebrow reveal-on-load">${esc(s.eyebrow)}</p>
              <h1 class="wordmark reveal-on-load">${esc(s.wordmark)}</h1>
              <p class="slogan reveal-on-load">${esc(s.slogan)}</p>
              <p class="lead reveal-on-load">${esc(s.lead)}</p>
              <div class="hero-actions reveal-on-load">${actions}</div>
              <div class="hero-meta reveal-on-load">${meta}</div>
            </div>
            <div class="hero-visual" id="heroVisual">
              <div class="editor-card">
                <div class="mock-bar">
                  <span>slogan.tex</span><span class="badge blue">XeLaTeX</span>
                </div>
                <pre class="type-lines" id="typeLines" aria-label="slogan.tex 编辑器示例"></pre>
              </div>
              <div class="paper-card">
                <div class="paper-title">LaTeXcellence</div>
                <div class="paper-author">智能体论文写作助手</div>
                <p class="paper-abs">
                  从一段自然语言到可编译的 LaTeX 工程，
                  智能体真正读写文件、编译验证，而不是只停留在对话。
                </p>
                <div class="paper-meta">
                  <span>\\documentclass{ctexart}</span>
                  <span class="paper-ok compiling" id="heroPdfStatus"
                    ><svg class="icon"><use href="#i-refresh" /></svg
                    ><span id="heroPdfStatusText">compiling…</span></span
                  >
                </div>
              </div>
              <div class="connect-dots" aria-hidden="true"></div>
            </div>
          </div>
          <div class="hero-hint" aria-hidden="true">
            <svg class="icon"><use href="#i-arrow" /></svg
            ><span>滚动，让文字逐渐生长</span>
          </div>
        </div>
      </section>`;
}

/* 第一幕 · 摩擦 / 第二幕 · 解法（共享 scene-copy 骨架 + 自定义舞台） */
function renderAct(s) {
  const points = s.points.map((p) => `<li>${esc(p)}</li>`).join("\n");
  const stage =
    s.stage.tpl === "friction" ? frictionStage(s.stage) : answerStage(s.stage);
  return `${sceneOpen(s, s.cls)}
        <div class="pin">
          ${glyphsHtml(s.glyphs)}
          <div class="scene-inner">
            <p class="ghost-num" data-parallax="-0.08">${esc(s.ghost)}</p>
            <div class="scene-copy">
              <p class="kicker text-small-caps">${esc(s.kicker)}</p>
              <h2 class="scene-title">${esc(s.title)}</h2>
              <p class="scene-desc">${lines(s.desc)}</p>
              <ul class="scene-points">${points}</ul>
            </div>
            <div class="scene-stage ${s.stageCls}">${stage}</div>
          </div>
        </div>
      </section>`;
}

function frictionStage(st) {
  const termLines = st.terminal.lines
    .map((l) => `<span class="${l.cls}">${esc(l.text)}</span>`)
    .join("\n");
  return `
              <div class="terminal-card">
                <div class="mock-bar">
                  <span>${esc(st.terminal.bar)}</span
                  ><span class="badge ${st.terminal.badgeCls}">${esc(st.terminal.badge)}</span>
                </div>
                <pre class="term-body">${termLines}</pre>
              </div>
              <div class="chat-fail">
                <div class="msg user">${esc(st.chat.user)}</div>
                <div class="msg assistant">${esc(st.chat.assistant)}</div>
              </div>`;
}

function answerStage(st) {
  const loop = st.loop
    .map(
      (t) =>
        `<div class="step done"><span class="sno"></span><span>${esc(t)}</span><span class="sline"></span></div>`,
    )
    .join("\n");
  return `
              <div class="marker-card">
                <div class="mock-bar">
                  <span>${esc(st.marker.bar)}</span
                  ><span class="badge ${st.marker.badgeCls}">${esc(st.marker.badge)}</span>
                </div>
                <div class="marker-row">
                  <pre class="code-block">${st.marker.beforeHtml}</pre>
                  <span class="marker-arrow"
                    ><svg class="icon"><use href="#i-swap" /></svg
                  ></span>
                  <pre class="code-block"><span class="c-marker">${esc(st.marker.afterText)}</span></pre>
                </div>
                <div class="marker-note">${st.marker.note}</div>
              </div>
              <div class="agent-loop">${loop}</div>`;
}

/* §1–§6 功能章节：kicker/title/desc + 副卡片 + 主 feature 卡片 */
function renderChapter(s) {
  const f = s.feature;
  const subCards = s.subCards.length
    ? `<div class="sub-cards">${s.subCards
        .map(
          (c) => `
                <article class="sub-card" id="${c.id}">
                  <span class="fc-icon"
                    ><svg class="icon"><use href="#${c.icon}" /></svg
                  ></span>
                  <div>
                    <h3 class="fc-title">${esc(c.title)}</h3>
                    <p class="fc-desc">${esc(c.desc)}</p>
                  </div>
                </article>`,
        )
        .join("\n")}
              </div>`
    : "";
  const mockBar = `<div class="mock-bar">
                    <span>${esc(f.mock.bar)}</span
                    >${
                      f.mock.badge
                        ? `<span class="badge ${f.mock.badgeCls}">${esc(f.mock.badge)}</span>`
                        : ""
                    }
                  </div>`;
  const mockBody = f.mock.body.map(mockBlock).join("\n");
  return `${sceneOpen(s)}
        <div class="pin">
          ${glyphsHtml(s.glyphs)}
          <div class="scene-inner">
            <p class="ghost-num" data-parallax="-0.1">${esc(s.ghost)}</p>
            <div class="scene-copy">
              <p class="kicker text-small-caps">${esc(s.kicker)}</p>
              <h2 class="scene-title">${esc(s.title)}</h2>
              <p class="scene-desc">${lines(s.desc)}</p>
              ${subCards}
            </div>
            <div class="scene-stage">
              <article class="feature-card" id="${f.id}">
                <div class="fc-head">
                  <span class="fc-icon"
                    ><svg class="icon"><use href="#${f.icon}" /></svg
                  ></span>
                  <div>
                    <h3 class="fc-title">${esc(f.title)}</h3>
                    <div class="fc-tag">${esc(f.tag)}</div>
                  </div>
                </div>
                <p class="fc-desc">${esc(f.desc)}</p>
                <div class="mock">
                  ${mockBar}
                  <div class="mock-body">
                    ${mockBody}
                  </div>
                </div>
              </article>
            </div>
          </div>
        </div>
      </section>`;
}

/* mock 内容块：按 type 渲染（类型见 js/content.js 注释） */
function mockBlock(b) {
  switch (b.type) {
    case "col":
      return `<div class="mock-col">${b.items.map(mockBlock).join("\n")}</div>`;
    case "formula-ocr":
      return `<div class="formula-demo" role="img" aria-label="公式图片示例"><span class="formula-demo-img" aria-hidden="true"></span></div>`;
    case "code":
      return `<pre class="code-block" data-code="${b.ref}"></pre>`;
    case "steps":
      return `<div class="steps">${b.items
        .map(
          (t) =>
            `<div class="step done"><span class="sno"></span><span>${esc(t)}</span><span class="sline"></span></div>`,
        )
        .join("\n")}
              </div>`;
    case "row": {
      const styleAttr = b.style ? ` style="${b.style}"` : "";
      return `<div class="mock-row"${styleAttr}>${b.items.map(mockBlock).join("\n")}</div>`;
    }
    case "file-tree":
    case "terminal": {
      const styleAttr = b.style ? ` style="${b.style}"` : "";
      return `<div class="${b.type}" data-code="${b.ref}"${styleAttr}></div>`;
    }
    case "diff-del":
    case "diff-add":
      return `<div class="diff-line ${b.type === "diff-del" ? "diff-del" : "diff-add"}">${b.html}</div>`;
    case "diff-hunk":
      return `<div class="diff-hunk">${esc(b.text)}</div>`;
    case "badges":
      return `<div class="mock-row">${b.items
        .map((i) => `<span class="badge ${i.cls}">${esc(i.text)}</span>`)
        .join("\n")}
              </div>`;
    case "chat":
      return `<div class="chat">
                    <div class="msg user">${esc(b.user)}</div>
                    <div class="think">${b.think.map((t) => `<span>${esc(t)}</span>`).join("\n")}</div>
                    <div class="mock-row">${b.tools
                      .map((t) => `<span class="toolchip">${esc(t)}</span>`)
                      .join("\n")}</div>
                    <div class="msg assistant">${esc(b.assistant)}</div>
                    <div class="diff-line diff-add">${esc(b.add)}</div>
                  </div>`;
    default:
      return "";
  }
}

/* 设计理念 */
function renderPrinciples(s) {
  const cards = s.cards
    .map(
      (c) => `
              <article class="principle">
                <div class="pr-head">
                  <span class="pr-icon"
                    ><svg class="icon"><use href="#${c.icon}" /></svg
                  ></span>
                  <h3 class="pr-title">${esc(c.title)}</h3>
                </div>
                <p class="pr-desc">${esc(c.desc)}</p>
                <ul class="pr-points">${c.points.map((p) => `<li>${esc(p)}</li>`).join("\n")}</ul>
              </article>`,
    )
    .join("\n");
  return `${sceneOpen(s, "scene-principles")}
        <div class="pin">
          ${glyphsHtml(s.glyphs)}
          <div class="scene-inner principles-inner">
            <div class="principles-head">
              <p class="kicker text-small-caps">${esc(s.head.kicker)}</p>
              <h2 class="scene-title">${esc(s.head.title)}</h2>
              <p class="scene-desc">${esc(s.head.desc)}</p>
            </div>
            <div class="principle-grid">${cards}</div>
          </div>
        </div>
      </section>`;
}

/* 技术栈 */
function renderStack(s) {
  const cards = s.cards
    .map(
      (c) => `
              <div class="stack-card">
                <div class="stack-name">
                  <svg class="icon"><use href="#${c.icon}" /></svg>${esc(c.name)}
                </div>
                <div class="tags">${c.tags.map((t) => `<span class="tag">${esc(t)}</span>`).join("\n")}</div>
                <p class="fc-desc">${esc(c.desc)}</p>
              </div>`,
    )
    .join("\n");
  return `${sceneOpen(s, "scene-stack")}
        <div class="pin">
          ${glyphsHtml(s.glyphs)}
          <div class="scene-inner stack-inner">
            <div class="principles-head">
              <p class="kicker text-small-caps">${esc(s.head.kicker)}</p>
              <h2 class="scene-title">${esc(s.head.title)}</h2>
              <p class="scene-desc">${esc(s.head.desc)}</p>
            </div>
            <div class="stack-grid">${cards}</div>
          </div>
        </div>
      </section>`;
}

/* 主题 · 七种气质（色卡来自 THEMES 单一事实来源） */
function renderThemes(s) {
  const cards = THEMES.map(
    (t) => `
                <button class="theme-card" data-theme-option="${t.id}" type="button">
                  <span class="t-swatch" style="background: ${themeGradient(t)}"></span>
                  <span class="t-name">${esc(t.name)}</span
                  ><span class="t-sub">${t.id} · ${t.sub}</span>
                </button>`,
  ).join("\n");
  return `${sceneOpen(s, "scene-themes")}
        <div class="pin themes-pin">
          ${glyphsHtml(s.glyphs)}
          <div class="themes-viewport">
            <div class="themes-track" id="themesTrack">
              <div class="themes-head">
                <p class="kicker text-small-caps">${esc(s.head.kicker)}</p>
                <h2 class="scene-title">${esc(s.head.title)}</h2>
                <p class="scene-desc">${esc(s.head.desc)}</p>
                <p class="themes-scroll-hint"
                  ><svg class="icon"><use href="#i-arrow" /></svg
                  >${esc(s.head.hint)}</p
                >
              </div>
              <div class="theme-showcase">${cards}</div>
            </div>
          </div>
        </div>
      </section>`;
}

/* 尾声 */
function renderOutro(s) {
  const actions = s.actions.map(actionHtml).join("\n");
  return `${sceneOpen(s, "scene-outro")}
        <div class="pin">
          ${glyphsHtml(s.glyphs)}
          <div class="scene-inner outro-inner">
            <p class="eyebrow">${esc(s.eyebrow)}</p>
            <h2 class="outro-title">${s.title}</h2>
            <p class="scene-desc">${esc(s.desc)}</p>
            <div class="hero-actions">${actions}</div>
          </div>
        </div>
      </section>`;
}

/* ---------- 场景分发 ---------- */
function renderScene(s) {
  switch (s.tpl) {
    case "hero":
      return renderHero(s);
    case "act":
      return renderAct(s);
    case "chapter":
      return renderChapter(s);
    case "principles":
      return renderPrinciples(s);
    case "stack":
      return renderStack(s);
    case "themes":
      return renderThemes(s);
    case "outro":
      return renderOutro(s);
    default:
      return "";
  }
}

/* ---------- 入口：一次性渲染全部由数据驱动的骨架 ---------- */
function renderAll() {
  const rail = document.getElementById("sceneRail");
  if (rail) {
    rail.innerHTML = NAV.map(
      (n) =>
        `<a class="rail-item" href="#${n.id}" data-rail="${n.id}"><span class="rail-no">${esc(n.no)}</span><span class="rail-name">${esc(n.name)}</span></a>`,
    ).join("\n");
  }

  const popover = document.getElementById("themePopover");
  if (popover) {
    popover.innerHTML =
      '<div class="popover-label">主题 · Theme</div>' +
      THEMES.map(
        (t) =>
          `<button class="theme-option" data-theme-option="${t.id}" type="button"><span class="swatch" style="background: ${t.swatch}"></span><span class="tname">${esc(t.name)}</span><span class="tcheck"></span></button>`,
      ).join("\n");
  }

  const scenes = document.getElementById("scenes");
  if (scenes) {
    scenes.innerHTML = SCENES.map(renderScene).join("\n");
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", renderAll);
} else {
  renderAll();
}
