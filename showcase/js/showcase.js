/* ============================================================
 * LaTeXcellence · Showcase
 * 主题切换 / 明暗切换 / 入场动画 / 弹层交互 / 代码块注入
 * 依赖：render.js（先生成 DOM）、content.js（代码块内容）、themes.js（默认主题）
 * ============================================================ */

import { CODE_BLOCKS } from "./content.js";
import { DEFAULT_THEME } from "./themes.js";

const STORE_KEY = "showcase-theme";
const DARK_KEY = "showcase-dark";
const root = document.documentElement;

/* ---------- 状态恢复 ---------- */
let savedTheme = null;
try {
  savedTheme = localStorage.getItem(STORE_KEY);
} catch (e) {
  /* localStorage 不可用时保持默认 */
}
if (!savedTheme) savedTheme = DEFAULT_THEME;

let savedDark = null;
try {
  savedDark = localStorage.getItem(DARK_KEY);
} catch (e) {
  /* 同上 */
}
const isDark =
  savedDark === null
    ? window.matchMedia("(prefers-color-scheme: dark)").matches
    : savedDark === "1";

function applyTheme(theme) {
  root.dataset.theme = theme;
  try {
    localStorage.setItem(STORE_KEY, theme);
  } catch (e) {
    /* 隐私模式等场景下忽略 */
  }
  syncThemeUI();
}

function applyDark(dark) {
  root.classList.toggle("dark", dark);
  try {
    localStorage.setItem(DARK_KEY, dark ? "1" : "0");
  } catch (e) {
    /* 同上 */
  }
  syncDarkIcon();
  syncFavicon();
}

/* ---------- UI 同步 ---------- */
function syncThemeUI() {
  const current = root.dataset.theme;
  document.querySelectorAll("[data-theme-option]").forEach((el) => {
    const active = el.getAttribute("data-theme-option") === current;
    el.classList.toggle("active", active);
    const check = el.querySelector(".tcheck");
    if (check) {
      check.textContent = active ? "✓" : "";
    }
  });
}

function syncDarkIcon() {
  const icon = document.getElementById("darkIcon");
  if (!icon) return;
  const use = icon.querySelector("use");
  if (use) {
    use.setAttribute(
      "href",
      root.classList.contains("dark") ? "#i-sun" : "#i-moon",
    );
  }
}

/* favicon（同前端 useFavicon：跟随明暗切换） */
function syncFavicon() {
  const link = document.querySelector("link[rel='icon']");
  if (!link) return;
  link.href = root.classList.contains("dark")
    ? "assets/favicon-dark.svg"
    : "assets/favicon.svg";
}

/* ---------- 主题弹层 ---------- */
const themeBtn = document.getElementById("themeBtn");
const themePopover = document.getElementById("themePopover");

function closePopover() {
  if (themePopover) themePopover.hidden = true;
}
function togglePopover() {
  if (!themePopover) return;
  themePopover.hidden = !themePopover.hidden;
}

if (themeBtn) {
  themeBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    togglePopover();
  });
}
document.addEventListener("click", (e) => {
  if (!themePopover || themePopover.hidden) return;
  if (!themeBtn || !themeBtn.contains(e.target)) closePopover();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closePopover();
});

document.querySelectorAll("[data-theme-option]").forEach((el) => {
  el.addEventListener("click", () => {
    applyTheme(el.getAttribute("data-theme-option"));
    closePopover();
  });
});

const darkBtn = document.getElementById("darkBtn");
if (darkBtn) {
  darkBtn.addEventListener("click", () => {
    applyDark(!root.classList.contains("dark"));
  });
}

/* ---------- 入场动画（IntersectionObserver，同 reveal） ---------- */
const revealEls = document.querySelectorAll(".reveal");
if ("IntersectionObserver" in window) {
  const io = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add("in");
          io.unobserve(entry.target);
        }
      });
    },
    { threshold: 0.12, rootMargin: "0px 0px -40px 0px" },
  );
  revealEls.forEach((el) => io.observe(el));
} else {
  revealEls.forEach((el) => el.classList.add("in"));
}

/* ---------- 代码块内容注入（内容存于 content.js，避免 HTML 缩进被 white-space: pre 原样显示） ---------- */
document.querySelectorAll("[data-code]").forEach((el) => {
  const html = CODE_BLOCKS[el.getAttribute("data-code")];
  if (html !== undefined) el.innerHTML = html;
});

/* ---------- 初始化 ---------- */
applyTheme(savedTheme);
applyDark(isDark);
