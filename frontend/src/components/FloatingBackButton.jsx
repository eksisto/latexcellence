import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { ArrowLeft } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/** 滚动越过该阈值后，悬浮返回按钮才淡入显示。 */
const SHOW_THRESHOLD = 240;

/**
 * 页面左下角悬浮返回按钮。
 * 页面滚动到中下部时淡入，回到上部时淡出，点击跳转到 `to`（默认欢迎页）。
 */
export default function FloatingBackButton({ to = "/" }) {
  const { t } = useTranslation("common");
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const onScroll = () => setVisible(window.scrollY > SHOW_THRESHOLD);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <Button
      variant="outline"
      nativeButton={false}
      render={<Link to={to} aria-label={t("back")} />}
      className={cn(
        "fixed bottom-6 left-6 z-40 h-10 gap-2 rounded-xl px-4 shadow-lg transition-all duration-300",
        visible
          ? "translate-y-0 opacity-100"
          : "pointer-events-none translate-y-2 opacity-0",
      )}
    >
      <ArrowLeft className="size-4" />
      <span>{t("back")}</span>
    </Button>
  );
}
