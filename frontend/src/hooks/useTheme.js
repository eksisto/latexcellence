import { useEffect, useCallback, useSyncExternalStore } from "react";

const STORAGE_KEY = "latexcellence-theme";

function getSystemTheme() {
  if (window.matchMedia("(prefers-color-scheme: dark)").matches) {
    return "dark";
  }
  return "light";
}

function getStoredTheme() {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === "light" || stored === "dark") return stored;
  } catch {
    /* localStorage unavailable */
  }
  return null;
}

function setThemeClass(resolved) {
  const root = document.documentElement;
  if (resolved === "dark") {
    root.classList.add("dark");
  } else {
    root.classList.remove("dark");
  }
}

function applyTheme(resolved) {
  // 优先使用 View Transitions API：将整页切换作为一次合成层交叉淡入，
  // 避免逐元素 transition 因主线程忙碌而"慢半拍"，且性能更好。
  // 若浏览器不支持或用户偏好减少动画，则退回直接切换（由 CSS transition 兜底）。
  const prefersReducedMotion = window.matchMedia(
    "(prefers-reduced-motion: reduce)",
  ).matches;

  if (!document.startViewTransition || prefersReducedMotion) {
    setThemeClass(resolved);
    return;
  }

  document.startViewTransition(() => {
    setThemeClass(resolved);
  });
}

/* ---------- 共享外部存储：所有 useTheme() 消费者共享同一份状态 ---------- */

let themeState = getStoredTheme() ?? getSystemTheme();
let listeners = new Set();

function emitChange() {
  applyTheme(themeState);
  listeners.forEach((l) => l());
}

function subscribe(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot() {
  return themeState;
}

function setThemeValue(next) {
  if (next === themeState) return;
  themeState = next;
  try {
    localStorage.setItem(STORAGE_KEY, next);
  } catch {
    /* localStorage unavailable */
  }
  emitChange();
}

/* 初始化：确保 DOM 在首次加载时也同步 */
applyTheme(themeState);

export function useTheme() {
  const resolved = useSyncExternalStore(subscribe, getSnapshot);

  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const handler = (e) => {
      if (!getStoredTheme()) {
        setThemeValue(e.matches ? "dark" : "light");
      }
    };
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, []);

  const setTheme = useCallback((theme) => {
    setThemeValue(theme);
  }, []);

  const toggleTheme = useCallback(() => {
    setThemeValue(themeState === "dark" ? "light" : "dark");
  }, []);

  return { resolved, setTheme, toggleTheme };
}
