import { useState, useEffect, useCallback } from "react";

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

export function useTheme() {
  const [resolved, setResolved] = useState(() => {
    const stored = getStoredTheme();
    const theme = stored ?? getSystemTheme();
    return theme;
  });

  useEffect(() => {
    applyTheme(resolved);
  }, [resolved]);

  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const handler = (e) => {
      if (!getStoredTheme()) {
        const next = e.matches ? "dark" : "light";
        setResolved(next);
      }
    };
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, []);

  const setTheme = useCallback((theme) => {
    setResolved(theme);
    try {
      localStorage.setItem(STORAGE_KEY, theme);
    } catch {
      /* localStorage unavailable */
    }
  }, []);

  const toggleTheme = useCallback(() => {
    setResolved((prev) => {
      const next = prev === "dark" ? "light" : "dark";
      try {
        localStorage.setItem(STORAGE_KEY, next);
      } catch {
        /* localStorage unavailable */
      }
      return next;
    });
  }, []);

  return { resolved, setTheme, toggleTheme };
}
