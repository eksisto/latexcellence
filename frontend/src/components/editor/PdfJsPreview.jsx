import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import {
  clampScale,
  fitWidthScale,
  flipToSynctexY,
  rectForDisplay,
  SCALE_MAX,
  SCALE_MIN,
  stepScale,
  synctexRectToCssRect,
} from "@/lib/editor/synctexMath";

import LoaderCircle from "lucide-react/dist/esm/icons/loader-circle.mjs";
import MoveHorizontal from "lucide-react/dist/esm/icons/move-horizontal.mjs";
import TriangleAlert from "lucide-react/dist/esm/icons/triangle-alert.mjs";
import ZoomIn from "lucide-react/dist/esm/icons/zoom-in.mjs";
import ZoomOut from "lucide-react/dist/esm/icons/zoom-out.mjs";

// worker 资产经 Vite ?url 引入：仅登记 URL 字符串（构建产物中的哈希资产路径），
// 不加载任何 pdf.js 代码；worker 文件本身在首次渲染时才被 worker 拉取
import pdfWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";

/**
 * pdf.js 渲染层单例（决策 Q2）：`pdfjs-dist` 动态 import 独立分块
 * （vite manualChunks: vendor-pdfjs），首屏零加载；worker 资产经 ?url
 * 引入。沿用 formulaHover.loadKatex 的单例模式——首次进入预览才加载，
 * 之后复用同一 Promise。
 */
let pdfjsPromise = null;
function loadPdfjs() {
  if (!pdfjsPromise) {
    pdfjsPromise = import("pdfjs-dist").then((pdfjs) => {
      pdfjs.GlobalWorkerOptions.workerSrc = pdfWorkerUrl;
      return pdfjs;
    });
  }
  return pdfjsPromise;
}

/**
 * PdfJsPreview —— pdf.js 渲染的 PDF 预览（editor v3 阶段一，替换
 * PDFPreview.jsx 的 iframe 实现，对外 props 兼容 {url}）。
 *
 * - 渲染：连续滚动 + IntersectionObserver 邻域懒渲染（视口外 600px），
 *   无文本层（非目标：选择/复制/搜索）；canvas 由本组件以 data-scale
 *   标记在 placeholder 内命令式管理，React 只持有页面占位盒。
 * - 工具条（最小集，替代原浏览器自带控件）：± 缩放（含 Ctrl+滚轮）、
 *   适宽、页码输入；缩放态跨编译（url 更新）保留。
 * - 正向同步（决策 Q1+Q4）：父级完成 synctex 查询后经 forwardTarget
 *   传入 `{records: [{page,x,y,w,h}]}`（72dpi 点、左上原点），组件滚动
 *   到对应页并绘制高亮浮层（synctexRectToCssRect 点→CSS 像素换算），
 *   下一次 forward 替换。
 * - 反向同步（决策 Q5）：双击取点 → viewport.convertToPdfPoint →
 *   flipToSynctexY 左上原点换算 → onBackwardSync({page, x, y})（单击
 *   保留给滚动/选择习惯）。
 */

/** 正向高亮浮层的样式（amber，与低置信角标同源警示色） */
const HIGHLIGHT_CLASS =
  "pointer-events-none absolute rounded-[1px] bg-amber-300/40 ring-1 ring-amber-400/70";

export default function PdfJsPreview({
  url,
  forwardTarget = null,
  onBackwardSync = null,
}) {
  const { t } = useTranslation("components");
  const [status, setStatus] = useState("idle"); // idle | loading | ready | error
  const [doc, setDoc] = useState(null);
  const [numPages, setNumPages] = useState(0);
  // 每页基准尺寸（scale=1 时的 pt 宽高），占位盒按 scale 线性放大
  const [pageSizes, setPageSizes] = useState([]);
  const [scale, setScale] = useState(1);
  const [currentPage, setCurrentPage] = useState(1);
  const [pageInput, setPageInput] = useState("1");
  // 正向同步高亮：{page, records}（records 为后端原始点单位记录）
  const [highlight, setHighlight] = useState(null);
  // IntersectionObserver 邻域集合的版本号（驱动懒渲染 effect 重跑）
  const [visibleVersion, setVisibleVersion] = useState(0);

  const containerRef = useRef(null);
  const holdersRef = useRef([]);
  const visiblePagesRef = useRef(new Set());
  const activeTasksRef = useRef(new Set());
  // 上一次懒渲染的 {doc, scale}：仅换文档/换缩放时取消在途渲染任务，
  // 纯滚动（visibleVersion 变化）不取消，避免打断即将完成的绘制
  const lastRunRef = useRef(null);
  // 渲染纪元：换文档/卸载后，在途 getPage 的迟到结果按纪号丢弃
  const renderEpochRef = useRef(0);
  // 首次就绪的适宽只做一次；此后跨编译保留用户缩放态
  const didFitRef = useRef(false);
  // 缩放滚动锚点：换档前记录（顶部页, 页内偏移比例），布局后恢复
  const pendingAnchorRef = useRef(null);
  const scaleRef = useRef(scale);

  useLayoutEffect(() => {
    scaleRef.current = scale;
  }, [scale]);

  /* ── 文档加载：url 变化即重载（缩放态保留，高亮与画布清空） ────── */
  useEffect(() => {
    if (!url) {
      setStatus("idle");
      setDoc(null);
      setNumPages(0);
      setPageSizes([]);
      return undefined;
    }
    let cancelled = false;
    let loaded = null;
    setStatus("loading");
    setHighlight(null);
    setDoc(null);
    setNumPages(0);
    setPageSizes([]);
    // 旧画布命令式移除：url 换新后 data-scale 判定会误认为已渲染
    for (const el of holdersRef.current) {
      el?.querySelectorAll("canvas").forEach((canvas) => canvas.remove());
    }
    renderEpochRef.current += 1;
    for (const task of activeTasksRef.current) {
      try {
        task.cancel();
      } catch {
        // 已结束的任务 cancel 不抛错，这里防御性吞掉
      }
    }
    activeTasksRef.current.clear();
    loadPdfjs()
      .then(async (pdfjs) => {
        loaded = await pdfjs.getDocument({
          url,
          // pdf.js 翻译无 ToUnicode 的 CID 字体（xdvipdfmx 嵌入的中文
          // Fandol 等，ctex 默认字库）需按 /CIDSystemInfo 拉取预置 CMap
          // （实测请求 Adobe-GB1-UCS2）：缺失时 translateFont 整体失败，
          // 中文字形不渲染（桌面查看器直接按 CID 绘制字形不受影响）。
          // 资源经 predev/prebuild 钩子拷贝到 public/pdfjs/cmaps
          // （src/lib/editor/pdfjsCmaps.build.mjs，生成物不入库）
          cMapUrl: `${import.meta.env.BASE_URL}pdfjs/cmaps/`,
          cMapPacked: true,
        }).promise;
        if (cancelled) {
          loaded.destroy();
          return;
        }
        const sizes = [];
        for (let p = 1; p <= loaded.numPages; p += 1) {
          // 预取每页基准尺寸（页面对象由 pdf.js 缓存，后续渲染零重复解析）
          const page = await loaded.getPage(p);
          const base = page.getViewport({ scale: 1 });
          sizes.push({ width: base.width, height: base.height });
        }
        if (cancelled) {
          loaded.destroy();
          return;
        }
        setDoc(loaded);
        setPageSizes(sizes);
        setNumPages(loaded.numPages);
        setCurrentPage(1);
        setPageInput("1");
        setStatus("ready");
      })
      .catch((e) => {
        if (cancelled) return;
        console.warn("PdfJsPreview: PDF 加载失败", e);
        setStatus("error");
      });
    return () => {
      cancelled = true;
      renderEpochRef.current += 1;
      for (const task of activeTasksRef.current) {
        try {
          task.cancel();
        } catch {
          // 同上
        }
      }
      activeTasksRef.current.clear();
      if (loaded) {
        loaded.destroy().catch(() => {});
      }
      setDoc(null);
      setNumPages(0);
      setPageSizes([]);
    };
  }, [url]);

  /* ── IntersectionObserver：邻域集合 + 页码指示 ─────────────────── */
  useEffect(() => {
    if (status !== "ready" || numPages === 0) return undefined;
    const root = containerRef.current;
    if (!root) return undefined;
    holdersRef.current.length = numPages;
    const observer = new IntersectionObserver(
      (entries) => {
        let changed = false;
        for (const entry of entries) {
          const idx = Number(entry.target.dataset.page);
          if (Number.isNaN(idx)) continue;
          if (entry.isIntersecting) {
            if (!visiblePagesRef.current.has(idx)) {
              visiblePagesRef.current.add(idx);
              changed = true;
            }
          } else if (visiblePagesRef.current.delete(idx)) {
            changed = true;
          }
        }
        if (changed) setVisibleVersion((v) => v + 1);
        // 页码指示：容器顶部下探 1/3 高度处命中的最上页
        const rootRect = root.getBoundingClientRect();
        const probe = rootRect.top + Math.min(rootRect.height / 3, 120);
        let top = 0;
        for (let i = 0; i < holdersRef.current.length; i += 1) {
          const el = holdersRef.current[i];
          if (!el) continue;
          if (el.getBoundingClientRect().bottom > probe) {
            top = i + 1;
            break;
          }
        }
        if (top > 0) {
          setCurrentPage((cur) => (top !== cur ? top : cur));
        }
      },
      { root, rootMargin: "600px 0px" },
    );
    holdersRef.current.forEach((el) => el && observer.observe(el));
    return () => observer.disconnect();
  }, [status, numPages]);

  /* ── 懒渲染：邻域页在当前 scale 绘制；不可见页的旧 scale 画布移除 ── */
  useLayoutEffect(() => {
    if (!doc || pageSizes.length === 0) return undefined;
    const prevRun = lastRunRef.current;
    const docOrScaleChanged =
      !prevRun || prevRun.doc !== doc || prevRun.scale !== scale;
    if (docOrScaleChanged) {
      for (const task of activeTasksRef.current) {
        try {
          task.cancel();
        } catch {
          // 同上
        }
      }
      activeTasksRef.current.clear();
    }
    lastRunRef.current = { doc, scale };
    const epoch = renderEpochRef.current;
    let cancelled = false;
    const scaleStr = String(scale);
    const dpr = Math.min(window.devicePixelRatio || 1, 2);

    const renderPage = async (pageIndex) => {
      const holder = holdersRef.current[pageIndex];
      if (!holder) return;
      const existing = holder.querySelector("canvas[data-scale]");
      if (existing && existing.dataset.scale === scaleStr) return;
      let page;
      try {
        page = await doc.getPage(pageIndex + 1);
      } catch {
        return;
      }
      if (cancelled || epoch !== renderEpochRef.current) return;
      const viewport = page.getViewport({ scale });
      let canvas = existing;
      if (!canvas) {
        canvas = document.createElement("canvas");
        holder.insertBefore(canvas, holder.firstChild);
      }
      canvas.width = Math.floor(viewport.width * dpr);
      canvas.height = Math.floor(viewport.height * dpr);
      canvas.style.width = `${Math.floor(viewport.width)}px`;
      canvas.style.height = `${Math.floor(viewport.height)}px`;
      canvas.dataset.scale = scaleStr;
      const task = page.render({
        canvasContext: canvas.getContext("2d"),
        viewport,
        transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : undefined,
      });
      activeTasksRef.current.add(task);
      try {
        await task.promise;
      } catch {
        // 渲染被取消（换缩放 / 换文档 / 卸载），画布由下一轮接管
      } finally {
        activeTasksRef.current.delete(task);
      }
    };

    for (let i = 0; i < holdersRef.current.length; i += 1) {
      const holder = holdersRef.current[i];
      if (!holder) continue;
      if (visiblePagesRef.current.has(i)) {
        renderPage(i);
      } else {
        const canvas = holder.querySelector("canvas[data-scale]");
        if (canvas && canvas.dataset.scale !== scaleStr) canvas.remove();
      }
    }
    return () => {
      cancelled = true;
    };
  }, [doc, scale, visibleVersion, pageSizes]);

  /* ── 滚动定位与缩放锚点 ───────────────────────────────────────── */
  const scrollToPage = useCallback((page) => {
    const container = containerRef.current;
    const el = holdersRef.current[page - 1];
    if (!container || !el) return;
    container.scrollTop +=
      el.getBoundingClientRect().top -
      container.getBoundingClientRect().top -
      8;
  }, []);

  /** 换缩放：先记录视口顶部页与页内偏移比例，布局后恢复锚点 */
  const applyScale = useCallback(
    (next) => {
      const clamped = clampScale(next);
      const container = containerRef.current;
      if (!container || pageSizes.length === 0) {
        setScale(clamped);
        return;
      }
      const rootTop = container.getBoundingClientRect().top;
      let anchor = 0;
      let ratio = 0;
      for (let i = 0; i < holdersRef.current.length; i += 1) {
        const el = holdersRef.current[i];
        if (!el) continue;
        const rect = el.getBoundingClientRect();
        if (rect.bottom > rootTop) {
          anchor = i;
          ratio = rect.height > 0 ? (rootTop - rect.top) / rect.height : 0;
          break;
        }
      }
      pendingAnchorRef.current = { anchor, ratio };
      setScale(clamped);
    },
    [pageSizes.length],
  );

  // 缩放后恢复滚动锚点（占位盒尺寸已随 state 同步更新）
  useLayoutEffect(() => {
    const pending = pendingAnchorRef.current;
    if (!pending) return;
    pendingAnchorRef.current = null;
    const container = containerRef.current;
    const el = holdersRef.current[pending.anchor];
    if (!container || !el) return;
    const height = el.getBoundingClientRect().height;
    const rect = el.getBoundingClientRect();
    const rootTop = container.getBoundingClientRect().top;
    container.scrollTop += rootTop + pending.ratio * height - rect.top;
  }, [scale]);

  /* ── Ctrl+滚轮缩放（非 passive，preventDefault 阻止浏览器整页缩放） ─ */
  useEffect(() => {
    const container = containerRef.current;
    if (!container || status !== "ready") return undefined;
    const onWheel = (e) => {
      if (!e.ctrlKey) return;
      e.preventDefault();
      applyScale(stepScale(scaleRef.current, e.deltaY < 0 ? 1 : -1));
    };
    container.addEventListener("wheel", onWheel, { passive: false });
    return () => container.removeEventListener("wheel", onWheel);
  }, [status, applyScale]);

  /* ── 首次就绪适宽（一次性；后续编译保留缩放态） ────────────────── */
  useLayoutEffect(() => {
    if (status !== "ready" || pageSizes.length === 0 || didFitRef.current)
      return;
    const container = containerRef.current;
    if (!container || container.clientWidth <= 50) return;
    didFitRef.current = true;
    setScale(fitWidthScale(container.clientWidth, pageSizes[0].width));
  }, [status, pageSizes]);

  /* ── 正向同步：forwardTarget 变化 → 替换高亮并滚动到目标页 ─────── */
  useEffect(() => {
    if (!doc) return;
    const records = forwardTarget?.records;
    const usable =
      Array.isArray(records) && records.length > 0
        ? { page: records[0].page, records }
        : null;
    setHighlight(usable);
    const page = usable?.page;
    if (Number.isInteger(page) && page >= 1 && page <= numPages) {
      scrollToPage(page);
    }
  }, [forwardTarget, doc, numPages, scrollToPage]);

  /* ── 反向同步：双击取点（单击保留给滚动/选择习惯，Q5） ─────────── */
  const handleDoubleClick = useCallback(
    async (e, pageIndex) => {
      if (!onBackwardSync || !doc) return;
      const canvas = e.currentTarget.querySelector("canvas[data-scale]");
      if (!canvas) return; // 未渲染页（尚未滚到）不取点
      const rect = canvas.getBoundingClientRect();
      const cssX = e.clientX - rect.left;
      const cssY = e.clientY - rect.top;
      if (cssX < 0 || cssY < 0 || cssX > rect.width || cssY > rect.height) {
        return;
      }
      let page;
      try {
        page = await doc.getPage(pageIndex + 1);
      } catch {
        return;
      }
      // convertToPdfPoint：CSS 像素（左上原点）→ PDF 点（左下原点）；
      // 再按 synctex 左上原点约定翻转（决策 Q5：y = pageHeight − y）
      const viewport = page.getViewport({ scale: scaleRef.current });
      const [pdfX, pdfY] = viewport.convertToPdfPoint(cssX, cssY);
      const pageHeightPt = pageSizes[pageIndex]?.height ?? viewport.viewBox[3];
      const round2 = (n) => Math.round(n * 100) / 100;
      onBackwardSync({
        page: pageIndex + 1,
        x: round2(pdfX),
        y: round2(flipToSynctexY(pdfY, pageHeightPt)),
      });
    },
    [doc, onBackwardSync, pageSizes],
  );

  /* ── 工具条动作 ───────────────────────────────────────────────── */
  const handleFitWidth = useCallback(() => {
    const container = containerRef.current;
    if (!container || pageSizes.length === 0) return;
    applyScale(fitWidthScale(container.clientWidth, pageSizes[0].width));
  }, [applyScale, pageSizes]);

  const commitPageInput = useCallback(() => {
    const parsed = Number.parseInt(pageInput, 10);
    if (Number.isInteger(parsed)) {
      const target = Math.min(numPages, Math.max(1, parsed));
      setCurrentPage(target);
      scrollToPage(target);
    }
    setPageInput(String(currentPage));
  }, [currentPage, numPages, pageInput, scrollToPage]);

  useEffect(() => {
    setPageInput(String(currentPage));
  }, [currentPage]);

  if (!url) return null; // 空态由 PDFPreview 处理

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* 最小工具条（Q2：替代原浏览器自带缩放/页码控件） */}
      <div className="flex shrink-0 items-center gap-1 border-b px-1.5 py-1 text-xs">
        <Button
          variant="ghost"
          size="icon"
          className="size-6"
          onClick={() => applyScale(stepScale(scale, -1))}
          disabled={status !== "ready" || scale <= SCALE_MIN}
          title={t("pdfPreview.zoomOut")}
          aria-label={t("pdfPreview.zoomOut")}
        >
          <ZoomOut className="size-3.5" />
        </Button>
        <span className="w-11 text-center tabular-nums text-muted-foreground">
          {Math.round(scale * 100)}%
        </span>
        <Button
          variant="ghost"
          size="icon"
          className="size-6"
          onClick={() => applyScale(stepScale(scale, 1))}
          disabled={status !== "ready" || scale >= SCALE_MAX}
          title={t("pdfPreview.zoomIn")}
          aria-label={t("pdfPreview.zoomIn")}
        >
          <ZoomIn className="size-3.5" />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="size-6"
          onClick={handleFitWidth}
          disabled={status !== "ready"}
          title={t("pdfPreview.fitWidth")}
          aria-label={t("pdfPreview.fitWidth")}
        >
          <MoveHorizontal className="size-3.5" />
        </Button>
        <span className="ml-auto flex items-center gap-1 tabular-nums text-muted-foreground">
          <input
            value={pageInput}
            onChange={(e) => setPageInput(e.target.value.replace(/[^\d]/g, ""))}
            onBlur={commitPageInput}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                commitPageInput();
              }
            }}
            disabled={status !== "ready"}
            aria-label={t("pdfPreview.pageInput")}
            className="h-6 w-10 rounded-md border bg-transparent px-1 text-center text-xs outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
          />
          <span>/ {numPages || "–"}</span>
        </span>
      </div>

      {/* 连续滚动渲染区：占位盒先行撑起滚动高度，canvas 邻域懒渲染 */}
      <div
        ref={containerRef}
        className="relative min-h-0 flex-1 overflow-auto bg-muted/40 px-3 py-3"
      >
        {status === "ready" &&
          pageSizes.map((size, i) => {
            const pageNumber = i + 1;
            const isTarget = highlight?.page === pageNumber;
            return (
              <div
                key={pageNumber}
                data-page={i}
                ref={(el) => {
                  holdersRef.current[i] = el;
                }}
                onDoubleClick={(e) => handleDoubleClick(e, i)}
                className="relative mx-auto mb-3 bg-white shadow-md ring-1 ring-black/10"
                style={{
                  width: Math.max(1, Math.round(size.width * scale)),
                  height: Math.max(1, Math.round(size.height * scale)),
                }}
              >
                {isTarget &&
                  highlight.records.map((record, j) => {
                    const rect = synctexRectToCssRect(
                      rectForDisplay(record),
                      scale,
                    );
                    return (
                      <div
                        key={j}
                        className={HIGHLIGHT_CLASS}
                        style={{
                          left: rect.left,
                          top: rect.top,
                          width: rect.width,
                          height: rect.height,
                        }}
                      />
                    );
                  })}
              </div>
            );
          })}
        {status === "loading" && (
          <div className="flex h-32 items-center justify-center gap-2 text-sm text-muted-foreground">
            <LoaderCircle className="size-4 animate-spin" />
            {t("pdfPreview.loading")}
          </div>
        )}
        {status === "error" && (
          <div className="flex h-32 items-center justify-center gap-2 text-sm text-destructive">
            <TriangleAlert className="size-4" />
            {t("pdfPreview.loadFailed")}
          </div>
        )}
      </div>
    </div>
  );
}
