import React, { useState, useCallback } from "react";
import { useTranslation } from "react-i18next";
import MarkerText from "./MarkerText";

export default function ParagraphContext({
  currentIndex,
  paragraphs,
  currentText,
}) {
  const { t } = useTranslation(["components", "common"]);
  const [open, setOpen] = useState(false);
  const toggle = useCallback(() => setOpen((v) => !v), []);
  const prevPara = paragraphs.find((p) => p.index === currentIndex - 1);
  const currentPara = paragraphs.find((p) => p.index === currentIndex);
  const nextPara = paragraphs.find((p) => p.index === currentIndex + 1);

  return (
    <div className="text-xs text-muted-foreground">
      <button
        type="button"
        onClick={toggle}
        className="flex items-center gap-1 cursor-pointer hover:text-foreground transition-colors select-none"
      >
        <svg
          className={`size-3 shrink-0 transition-transform duration-300 ${open ? "rotate-90" : ""}`}
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
        >
          <path d="M9 18l6-6-6-6" />
        </svg>
        {t("common:viewContext", { ns: "common" })}
      </button>
      <div
        className={`grid transition-all duration-300 ease-in-out ${open ? "grid-rows-[1fr]" : "grid-rows-[0fr]"}`}
      >
        <div className="overflow-hidden">
          <div className="mt-2 space-y-2 bg-muted/50 p-3 rounded-md border border-border/50">
            {prevPara && (
              <div className="opacity-60 hover:opacity-100 transition-opacity">
                <div className="text-[10px] font-mono mb-1 select-none text-muted-foreground">
                  P{prevPara.index}
                </div>
                <div className="whitespace-pre-wrap font-sans leading-relaxed">
                  <MarkerText text={prevPara.text} />
                </div>
              </div>
            )}
            <div className="bg-background p-2.5 rounded border border-border text-foreground shadow-sm">
              <div className="text-[10px] font-mono text-primary mb-1 select-none">
                P{currentIndex} {t("common:currentParagraph", { ns: "common" })}
              </div>
              <div className="whitespace-pre-wrap font-sans leading-relaxed">
                <MarkerText text={currentPara?.text || currentText} />
              </div>
            </div>
            {nextPara && (
              <div className="opacity-60 hover:opacity-100 transition-opacity">
                <div className="text-[10px] font-mono mb-1 select-none text-muted-foreground">
                  P{nextPara.index}
                </div>
                <div className="whitespace-pre-wrap font-sans leading-relaxed">
                  <MarkerText text={nextPara.text} />
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
