import { useEffect } from "react";
import useStore from "@/store/useStore";

const themeModules = import.meta.glob("./*.css", { eager: true });

const THEME_NAMES = {
  vercel: "幽冥黑",
  twitter: "景泰蓝",
  amber: "琥珀黄",
  violet: "凝夜紫",
  northern: "庭芜绿",
  tangerine: "海螺橙",
};

export function getThemeDisplayName(key) {
  return THEME_NAMES[key] || key;
}

const THEME_ORDER = ["tangerine", "amber", "northern", "twitter", "violet", "vercel"];

export function getAvailableThemes() {
  const keys = Object.keys(themeModules).map((path) => {
    const match = path.match(/\/(\w+)\.css$/);
    return match ? match[1] : path;
  });
  return keys.sort((a, b) => {
    const ia = THEME_ORDER.indexOf(a);
    const ib = THEME_ORDER.indexOf(b);
    return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib);
  });
}

export function useThemeManager() {
  const currentTheme = useStore((s) => s.currentTheme);
  const setCurrentTheme = useStore((s) => s.setCurrentTheme);

  useEffect(() => {
    document.documentElement.dataset.theme = currentTheme;
  }, [currentTheme]);

  return {
    currentTheme,
    setCurrentTheme,
    themes: getAvailableThemes(),
  };
}
