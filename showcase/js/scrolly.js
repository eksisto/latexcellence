/* ============================================================
 * LaTeXcellence · Showcase — Scrollytelling 滚动引擎
 * 全屏固定场景（--p 进度）+ Parallax 视差 + 行号栏 / 章节导轨 /
 * 编译进度条 / 编译状态角标 / Hero 打字机效果
 * 依赖：render.js（先生成 DOM）、content.js（打字机内容）、constants.js（调参常量）
 * ============================================================ */

import { LAYOUT } from "./constants.js";
import { TYPE_LINES } from "./content.js";

const reduceMotion = window.matchMedia(
  "(prefers-reduced-motion: reduce)",
).matches;
const root = document.documentElement;

/* 小屏 / 短屏 / 减弱动效 → 退化为普通堆叠页（内容全部可见，无固定场景） */
function decideLayout() {
  const stacked =
    reduceMotion ||
    window.innerWidth < LAYOUT.STACK_BREAKPOINT_W ||
    window.innerHeight < LAYOUT.STACK_BREAKPOINT_H;
  root.classList.toggle("no-pin", stacked);
  return stacked;
}
let stacked = decideLayout();

const pinned = Array.from(document.querySelectorAll("[data-pin]"));
const parallaxEls = Array.from(document.querySelectorAll("[data-parallax]"));
const progressFill = document.getElementById("progressFill");
const compileChip = document.getElementById("compileChip");
const compileText = document.getElementById("compileText");
const lgNumbers = document.getElementById("lgNumbers");
const themesTrack = document.getElementById("themesTrack");
const railItems = Array.from(document.querySelectorAll("[data-rail]"));
let lastGutterLine = -1;

function clamp01(v) {
  return v < 0 ? 0 : v > 1 ? 1 : v;
}

/* ---------- 行号栏 ---------- */
function updateGutter(y, vh) {
  if (!lgNumbers) return;
  const line = Math.max(1, Math.round(y / LAYOUT.GUTTER_LINE_H) + 1);
  if (line === lastGutterLine) return;
  lastGutterLine = line;
  const start = Math.max(1, line - Math.floor(LAYOUT.GUTTER_COUNT / 2));
  let html = "";
  for (let i = 0; i < LAYOUT.GUTTER_COUNT; i++) {
    const n = start + i;
    html += `<span${n === line ? ' class="cur"' : ""}>${n}</span>`;
  }
  lgNumbers.innerHTML = html;
}

/* ---------- 章节导轨高亮 ---------- */
function updateRail(scrollY, vh) {
  if (!railItems.length) return;
  const cy = scrollY + vh / 2;
  let activeId = null;
  for (let i = 0; i < pinned.length; i++) {
    const r = pinned[i].getBoundingClientRect();
    const top = r.top + scrollY;
    const bottom = top + pinned[i].offsetHeight;
    if (cy >= top && cy <= bottom) {
      activeId = pinned[i].id;
      break;
    }
  }
  for (let j = 0; j < railItems.length; j++) {
    railItems[j].classList.toggle(
      "active",
      railItems[j].getAttribute("data-rail") === activeId,
    );
  }
}

/* ---------- 主更新 ---------- */
let ticking = false;
function requestTick() {
  if (ticking) return;
  ticking = true;
  window.requestAnimationFrame(update);
}

function getScrollY() {
  return (
    window.pageYOffset ||
    document.documentElement.scrollTop ||
    window.scrollY ||
    0
  );
}

function getMaxScroll() {
  return Math.max(
    1,
    document.documentElement.scrollHeight - window.innerHeight,
  );
}

function update() {
  ticking = false;
  const vh = window.innerHeight;
  const y = getScrollY();
  const maxScroll = getMaxScroll();
  const progress = clamp01(y / maxScroll);

  if (progressFill) progressFill.style.width = progress * 100 + "%";

  if (compileChip) {
    const done = progress >= LAYOUT.PROGRESS_DONE;
    compileChip.classList.toggle("done", done);
    if (compileText) {
      compileText.textContent = done ? "intro.pdf 已生成" : "compiling…";
    }
    const use = compileChip.querySelector("use");
    if (use) {
      use.setAttribute("href", done ? "#i-check" : "#i-refresh");
    }
  }

  /* 固定场景进度 --p */
  for (let i = 0; i < pinned.length; i++) {
    const el = pinned[i];
    const rect = el.getBoundingClientRect();
    const len = el.offsetHeight - vh;
    const p = len > 0 ? clamp01(-rect.top / len) : 0;
    el.style.setProperty("--p", p.toFixed(4));
  }

  /* 视差 --py */
  for (let j = 0; j < parallaxEls.length; j++) {
    const pe = parallaxEls[j];
    const r = pe.getBoundingClientRect();
    const speed = parseFloat(pe.getAttribute("data-parallax")) || 0;
    const dy = r.top + r.height / 2 - vh / 2;
    pe.style.setProperty("--py", (dy * speed).toFixed(2));
  }

  /* 主题横向滑动距离 --tw */
  if (themesTrack) {
    const tr = themesTrack.getBoundingClientRect();
    const travel = Math.max(
      0,
      tr.width - window.innerWidth + LAYOUT.THEMES_TRAVEL_PAD,
    );
    themesTrack.style.setProperty("--tw", travel.toFixed(0));
  }

  updateGutter(y, vh);
  updateRail(y, vh);
}

window.addEventListener("scroll", requestTick, { passive: true });
window.addEventListener("resize", () => {
  stacked = decideLayout();
  cancelSnap();
  lastGutterLine = -1;
  markUnfit();
  invalidateSlots();
  requestTick();
});

/* ---------- 滚轮灵敏度增强 ---------- */
function wheelDeltaPx(e) {
  let d = e.deltaY;
  if (e.deltaMode === 1)
    d *= 16; // Firefox 行模式 → 像素
  else if (e.deltaMode === 2) d *= window.innerHeight; // 页模式 → 像素
  return d;
}

/* 命中元素或其祖先本身可滚动时，交给原生滚动（代码块 / 终端等） */
function insideScrollable(target) {
  let el = target;
  while (el && el.nodeType === 1 && el !== document.body) {
    const st = window.getComputedStyle(el);
    const scrollable =
      (el.scrollHeight > el.clientHeight + 1 &&
        /(auto|scroll|overlay)/.test(st.overflowY)) ||
      (el.scrollWidth > el.clientWidth + 1 &&
        /(auto|scroll|overlay)/.test(st.overflowX));
    if (scrollable) return true;
    el = el.parentElement;
  }
  return false;
}

/* ============================================================
 * 槽位（slot）滚动
 *
 * 需求：一格滚轮 = 一个分区，避免用户为看完一个分区滚动整段动画行程。
 * 实现：把每个分区“内容完整呈现”的位置（锚点，见 sceneAnchorTop）收集为
 * 有序停止点数组 stops，滚轮一格就在相邻停止点之间做平滑跳转。
 *
 * 停止点结构（computeSlots）：
 *   - 普通场景：1 个停止点 = 锚点 top + (高 - vh) × data-anchor；
 *   - 主题场景：起点（左端，锚点）与终点（右端 p=1，最右色卡）2 个停止点，
 *     二者之间为“连续滑动区”，滚轮不跳槽位、只平移色卡；
 *   - 最后一个场景：直接以页面最底部（maxScroll）为停止点，
 *     保证编译角标“intro.pdf 已生成”可达。
 *
 * 交互规则（handleWheel）：
 *   - 普通停止点之间：下滚 = 跳下一格，上滚 = 跳上一格；
 *   - 主题连续滑动区内：下滚/上滚连续平移色卡，到终点后下滚才跳下一分区，
 *     到起点后上滚才跳回上一分区；从页面最底部上滚先跳回最右色卡；
 *   - 无论滚轮幅度多大，一次最多移动一个分区：跳转动画进行中一律忽略
 *     滚轮事件，必须等当前分区切换完毕后再滚（不排队、不连跳）。
 *
 * 槽位锚点沿用场景 data-anchor（render.js 按模板写入，见 README 调参说明）。
 * ============================================================ */

/* 停止点缓存：布局（尺寸 / 字体 / 视口）变化后由 invalidateSlots() 清空，首次滚轮时重建 */
let slotCache = null;

/* 计算有序停止点 stops（升序）。布局变化后由 invalidateSlots() 失效重建。
   - 跳过 .unfit 兜底场景（内容超高已退化为普通堆叠块，不再作为槽位）；
   - 主题场景贡献起点 / 终点两个停止点，并额外记录 themesStart / themesEnd，
     供 handleWheel 判定“连续滑动区”；
   - 末尾场景不取自身锚点，直接以页面最底部（maxScroll）收尾。 */
function computeSlots() {
  const vh = window.innerHeight;
  const stops = [];
  let themesStart = null;
  let themesEnd = null;
  const scenes = pinned.filter((el) => !el.classList.contains("unfit"));
  for (let i = 0; i < scenes.length; i++) {
    const el = scenes[i];
    const rect = el.getBoundingClientRect();
    const top = rect.top + getScrollY();
    const len = Math.max(0, el.offsetHeight - vh);
    const isThemes = el.classList.contains("scene-themes");
    const isLast = i === scenes.length - 1;
    if (isThemes) {
      const raw = parseFloat(el.getAttribute("data-anchor"));
      const p = Number.isFinite(raw) ? raw : LAYOUT.RAIL_ANCHOR_RATIO;
      themesStart = top + len * p;
      themesEnd = top + len;
      stops.push(themesStart);
      if (!isLast) stops.push(themesEnd);
    } else if (isLast) {
      stops.push(getMaxScroll());
    } else {
      stops.push(sceneAnchorTop(el));
    }
  }
  slotCache = { stops, themesStart, themesEnd };
  return slotCache;
}

/* 取停止点（惰性计算：首次访问时构建并缓存） */
function getSlots() {
  return slotCache || computeSlots();
}

/* 布局变化（resize / 字体加载 / markUnfit 结果变化）后失效缓存，下次滚轮时重建 */
function invalidateSlots() {
  slotCache = null;
}

/* 二分查找：返回最后一个满足 stops[i] <= y + tol 的下标。
   tol = SNAP_TOL_PX 是“已停在槽位”的容差——滚动位置停在某停止点附近
   （如动画精确落点或滚动条拖动的亚像素误差）时仍视为已在该槽位，
   避免方向判断来回抖动。要求 stops 升序。 */
function floorIndex(stops, y, tol) {
  let lo = 0;
  let hi = stops.length - 1;
  let ans = 0;
  const t = y + tol;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (stops[mid] <= t) {
      ans = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return ans;
}

/* 缓动：起步极快、全程减速（easeInOut），模拟快速滑入后逐渐停稳。
   f(0)=0.1!=0 （因为从锚点开始是一部分位移没有任何视觉变化）、f(1)=1，
   关于 (0.5, 0.55) 中心对称, f'(0)=f'(1)=0。 */
function easeInOut(t) {
  t = Math.min(1, Math.max(0, t));
  return 0.1 + 2.7 * t * t - 1.8 * t * t * t;
}

/* 进行中的跳转动画：直接保存当前 rAF 回调 id（供取消）。
   动画期间滚轮事件一律忽略——最多滚动一个分区，须等切换完成后再滚。 */
let snapAnim = null;

/* 取消进行中的跳转动画。
   必须同时清掉内联 scroll-behavior，否则动画被中断（点击 / 缩放 / 新跳转）时会
   残留 scroll-behavior: auto，覆盖掉 showcase.css 里的 smooth。 */
function cancelSnap() {
  if (!snapAnim) return;
  cancelAnimationFrame(snapAnim);
  snapAnim = null;
  document.documentElement.style.scrollBehavior = "";
}

/* 从当前位置平滑滚动到 stops[targetIdx]。
   动画结束时精确写死落点，避免浮点误差停在目标前 1px。
   动画期间临时把 html 的 scroll-behavior 置为 auto，保证每帧
   scrollTo(behavior: "instant") 立即生效，不被 CSS 的 smooth 干扰。 */
function animateScrollTo(targetIdx) {
  const stops = getSlots().stops;
  const maxScroll = getMaxScroll();
  const target = Math.max(0, Math.min(stops[targetIdx], maxScroll));
  const from = getScrollY();
  if (snapAnim) cancelAnimationFrame(snapAnim); // 防御：正常流程下动画进行中不会走到这里
  if (Math.abs(target - from) < 1) {
    // 已在目标位置（<1px），无需动画
    snapAnim = null;
    return;
  }
  /* 动画期间临时关闭 CSS 的 scroll-behavior: smooth，保证逐帧 instant 写滚动生效 */
  const rootStyle = document.documentElement.style;
  const prevBehavior = rootStyle.scrollBehavior;
  rootStyle.scrollBehavior = "auto";
  const start = performance.now();
  const step = (now) => {
    const t = Math.min(1, (now - start) / LAYOUT.SNAP_MS);
    window.scrollTo({
      top: from + (target - from) * easeInOut(t),
      behavior: "instant",
    });
    if (t < 1) {
      snapAnim = requestAnimationFrame(step);
    } else {
      window.scrollTo({ top: target, behavior: "instant" }); // 精确落点
      rootStyle.scrollBehavior = prevBehavior;
      snapAnim = null;
    }
  };
  snapAnim = requestAnimationFrame(step);
}

/* 滚轮主逻辑：决定“连续滑动”还是“跳槽位”，并计算目标停止点下标。
   入参 dy 为归一化后的滚轮像素位移（见 wheelDeltaPx）。
   规则（无论滚轮幅度多大，一次最多移动一个分区）：
   1) 跳转动画进行中 → 直接忽略本次滚轮：必须先等当前分区切换完毕，
      再滚动滚轮才能去下一个分区（不排队、不连跳）；
   2) 空闲且位于主题连续滑动区 → 直接 scrollBy 平移色卡，但单次位移
      钳制在 [主题起点, 主题终点] 内，绝不跨出主题分区；
   3) 否则按下滚 / 上滚计算 targetIdx：
      - 下滚 → 当前槽位 +1；
      - 上滚 → 位于主题终点之后（含页面最底部）先回最右色卡，否则当前槽位 -1。
   4) 钳位到合法范围后交给 animateScrollTo 平滑执行。 */
function handleWheel(dy) {
  /* 切换动画进行中：最多滚动一个分区，必须等切换完后再滚 */
  if (snapAnim) return;

  const y = getScrollY();
  const { stops, themesStart, themesEnd } = getSlots();
  const tol = LAYOUT.SNAP_TOL_PX;
  const down = dy > 0;

  /* 空闲且位于主题内部：连续滑动色卡
     - 下滚：起点 → 终点前；上滚：终点 → 起点后
     - 单次位移钳制在区间内，避免单次大幅滚动跨出主题分区 */
  if (themesStart !== null && themesEnd !== null) {
    if (down && y >= themesStart - tol && y < themesEnd - tol) {
      const step = Math.min(dy * LAYOUT.WHEEL_SENSITIVITY, themesEnd - y);
      window.scrollBy({ top: step, behavior: "instant" });
      return;
    }
    if (!down && y > themesStart + tol && y <= themesEnd + tol) {
      const step = Math.max(dy * LAYOUT.WHEEL_SENSITIVITY, themesStart - y);
      window.scrollBy({ top: step, behavior: "instant" });
      return;
    }
  }

  if (!stops.length) return;
  let targetIdx;
  if (down) {
    targetIdx = floorIndex(stops, y, tol) + 1; // 停在当前槽位，下滚去下一格
  } else {
    /* 在主题终点之后（含页面最底部）上滚：先跳回最右色卡 */
    if (themesEnd !== null && y > themesEnd + tol) {
      targetIdx = stops.indexOf(themesEnd);
    } else {
      targetIdx = floorIndex(stops, y, tol) - 1; // 停在当前槽位，上滚回上一格
    }
  }
  targetIdx = Math.max(0, Math.min(targetIdx, stops.length - 1));
  animateScrollTo(targetIdx);
}

window.addEventListener(
  "wheel",
  (e) => {
    if (stacked || e.ctrlKey || e.metaKey) return; // 堆叠布局 / 缩放手势交给原生
    if (insideScrollable(e.target)) return; // 内部可滚动容器交给原生
    e.preventDefault();
    const dy = wheelDeltaPx(e);
    if (dy === 0) return;
    handleWheel(dy);
  },
  { passive: false },
);

/* ---------- 锚点跳转：按场景 data-anchor 进度定位 ----------
 * 进度由 render.js 按场景模板写入（取最后一个入场元素完全显示的阈值），
 * 避免停在 p=0 空白处或内容半入场的中间位置。
 */
function sceneAnchorTop(el) {
  const top = el.getBoundingClientRect().top + getScrollY();
  const vh = window.innerHeight;
  const len = Math.max(0, el.offsetHeight - vh);
  const raw = parseFloat(el.getAttribute("data-anchor"));
  const p = Number.isFinite(raw) ? raw : LAYOUT.RAIL_ANCHOR_RATIO;
  return top + len * p;
}

function sceneJumpTarget(hash) {
  if (!hash || hash === "#") return null;
  const el = document.getElementById(hash.slice(1));
  if (!el || !el.hasAttribute("data-pin")) return null;
  return sceneAnchorTop(el);
}

document.addEventListener("click", (e) => {
  const a =
    e.target && e.target.closest ? e.target.closest("a[href^='#']") : null;
  if (!a) return;
  if (a.id === "toTopBtn") {
    e.preventDefault();
    cancelSnap();
    window.scrollTo({ top: 0, behavior: "smooth" });
    return;
  }
  const target = sceneJumpTarget(a.getAttribute("href"));
  if (target !== null) {
    e.preventDefault();
    cancelSnap();
    window.scrollTo({ top: target, behavior: "smooth" });
  }
});

/* 打开页面时若带 #scene-xxx，同样跳到锚点目标位置 */
function handleInitialHash() {
  const target = sceneJumpTarget(window.location.hash);
  if (target === null) return;
  cancelSnap();
  window.scrollTo({ top: target, behavior: "auto" });
}

/* ---------- 纸张卡片 PDF 状态徽标（编译中 → 已生成） ---------- */
function setHeroPdfStatus(done) {
  const el = document.getElementById("heroPdfStatus");
  if (!el) return;
  el.classList.toggle("compiling", !done);
  const use = el.querySelector("use");
  if (use) {
    use.setAttribute("href", done ? "#i-check" : "#i-refresh");
  }
  const label = document.getElementById("heroPdfStatusText");
  if (label) {
    label.textContent = done ? "slogan.pdf 已生成" : "compiling…";
  }
}

/* ---------- Hero 编辑器打字机 ---------- */
function esc(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
function typeHero() {
  const el = document.getElementById("typeLines");
  if (!el) return;
  if (stacked) {
    el.textContent = TYPE_LINES.join("\n");
    setHeroPdfStatus(true);
    return;
  }
  setHeroPdfStatus(false);
  let lineIndex = 0;
  let charIndex = 0;
  let timer = null;
  function render() {
    let html = "";
    for (let i = 0; i < lineIndex; i++) {
      html += esc(TYPE_LINES[i]) + "\n";
    }
    if (lineIndex < TYPE_LINES.length) {
      html += esc(TYPE_LINES[lineIndex].slice(0, charIndex));
    }
    html += '<span class="type-caret"></span>';
    el.innerHTML = html;
  }
  function tick() {
    if (lineIndex >= TYPE_LINES.length) {
      setHeroPdfStatus(true);
      return;
    }
    const line = TYPE_LINES[lineIndex];
    if (charIndex <= line.length) {
      render();
      charIndex += 1;
      timer = setTimeout(tick, LAYOUT.TYPE_CHAR_MS);
    } else {
      lineIndex += 1;
      charIndex = 0;
      timer = setTimeout(tick, LAYOUT.TYPE_LINE_MS);
    }
  }
  render();
  timer = setTimeout(tick, LAYOUT.TYPE_START_MS);
}

/* ---------- 单场景超高兜底 ---------- */
function markUnfit() {
  if (stacked) return;
  const vh = window.innerHeight;
  for (let i = 0; i < pinned.length; i++) {
    const pin = pinned[i].querySelector(".pin");
    if (!pin) continue;
    /* 只统计真实内容高度：.ghost-num 的 --py 视差位移会撑大 scrollHeight，
       导致刷新时的滚动位置不同就误判 unfit（功能卡片从右侧并排退化成下方堆叠） */
    const inner = pin.querySelector(".scene-inner");
    const h = inner ? inner.getBoundingClientRect().height : pin.scrollHeight;
    pinned[i].classList.toggle("unfit", h > vh + LAYOUT.UNFIT_MARGIN);
  }
}

/* ---------- 初始化 ---------- */
function init() {
  typeHero();
  update();
  requestTick();
  markUnfit();
  invalidateSlots();
  if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(() => {
      markUnfit();
      invalidateSlots();
      requestTick();
    });
  }
  setTimeout(handleInitialHash, 60);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
