import React from 'react';
import { useTranslation } from "react-i18next";
import MarkerText from './MarkerText';

export default function ParagraphContext({ currentIndex, paragraphs, currentText }) {
  const { t } = useTranslation(["components", "common"]);
  const prevPara = paragraphs.find(p => p.index === currentIndex - 1);
  const nextPara = paragraphs.find(p => p.index === currentIndex + 1);

  return (
    <details className="text-xs text-muted-foreground group">
      <summary className="cursor-pointer hover:text-foreground transition-colors select-none">
        {t("common.viewContext", { ns: "common" })}
      </summary>
      <div className="mt-2 space-y-2 bg-muted/50 p-3 rounded-md border border-border/50">
        {prevPara && (
          <div className="opacity-60 hover:opacity-100 transition-opacity">
            <div className="text-[10px] font-mono mb-1 select-none text-muted-foreground">P{prevPara.index}</div>
            <div className="whitespace-pre-wrap font-sans leading-relaxed">
              <MarkerText text={prevPara.plain_text} />
            </div>
          </div>
        )}
        <div className="bg-background p-2.5 rounded border border-border text-foreground shadow-sm">
          <div className="text-[10px] font-mono text-primary mb-1 select-none">P{currentIndex} {t("common.currentParagraph", { ns: "common" })}</div>
          <div className="whitespace-pre-wrap font-sans leading-relaxed">
            <MarkerText text={currentText} />
          </div>
        </div>
        {nextPara && (
          <div className="opacity-60 hover:opacity-100 transition-opacity">
            <div className="text-[10px] font-mono mb-1 select-none text-muted-foreground">P{nextPara.index}</div>
            <div className="whitespace-pre-wrap font-sans leading-relaxed">
              <MarkerText text={nextPara.plain_text} />
            </div>
          </div>
        )}
      </div>
    </details>
  );
}