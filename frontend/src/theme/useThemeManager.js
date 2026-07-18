import { useEffect } from "react";
import useStore from "@/store/useStore";

const themeModules = import.meta.glob("./*.css", { eager: true });

const THEME_NAMES = {
  youminghei: "幽冥黑",
  jingtailan: "景泰蓝",
  hupohuang: "琥珀黄",
  ningyezi: "凝夜紫",
  tingwulv: "庭芜绿",
  hailuocheng: "海螺橙",
  yingtaohong: "樱桃红",
};

export function getThemeDisplayName(key) {
  return THEME_NAMES[key] || key;
}

const THEME_ORDER = [ "yingtaohong", "hailuocheng", "hupohuang", "tingwulv", "jingtailan", "ningyezi", "youminghei"];

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
