/**
 * StaleCheckBanner —— 检查结果保鲜横幅（editor-plan §8，决策 Q13）。
 *
 * 各逐段检查结果记录检查时的文件 MD5；检查页加载/结果就绪后经
 * /api/project/file-md5 比对当前 MD5，不一致时渲染"文件已在编辑器中
 * 修改，建议重新检查"。横幅仅提示，不销毁既有结果。
 */

import { useTranslation } from "react-i18next";
import TriangleAlert from "lucide-react/dist/esm/icons/triangle-alert.mjs";

export default function StaleCheckBanner({ files = [] }) {
  const { t } = useTranslation("components");
  if (!files || files.length === 0) return null;
  return (
    <div className="flex items-start gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-700 dark:text-amber-400">
      <TriangleAlert className="mt-0.5 size-4 shrink-0" />
      <div className="min-w-0">
        <p>{t("staleCheck.title")}</p>
        <ul className="mt-0.5 space-y-0.5 text-xs opacity-80">
          {files.map((file) => (
            <li key={file} className="truncate font-mono">
              {file}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
