import { useTranslation } from "react-i18next";
import {
  Globe,
  FileText,
  Search,
  PencilLine,
  Undo2,
  HelpCircle,
  FolderOpen,
  GraduationCap,
} from "lucide-react";

const scholarlyTools = [
  "search_scholarly_works",
  "get_work_details",
  "find_related_works",
  "get_full_text_or_sections",
  "search_research_data_resources",
];

const toolIconMap = {
  web_search: Globe,
  list_files: FolderOpen,
  read_file_content: FileText,
  get_section_structure: FileText,
  search_across_files: Search,
  edit_paragraph: PencilLine,
  undo_last_edit: Undo2,
  request_clarification: HelpCircle,
  search_scholarly_works: GraduationCap,
  get_work_details: GraduationCap,
  find_related_works: GraduationCap,
  get_full_text_or_sections: GraduationCap,
  search_research_data_resources: GraduationCap,
};

const toolColorMap = {
  web_search: "text-blue-500 bg-blue-500/5 border-blue-500/20",
  list_files: "text-emerald-500 bg-emerald-500/5 border-emerald-500/20",
  read_file_content: "text-emerald-500 bg-emerald-500/5 border-emerald-500/20",
  get_section_structure:
    "text-emerald-500 bg-emerald-500/5 border-emerald-500/20",
  search_across_files:
    "text-emerald-500 bg-emerald-500/5 border-emerald-500/20",
  edit_paragraph: "text-amber-500 bg-amber-500/5 border-amber-500/20",
  undo_last_edit: "text-purple-500 bg-purple-500/5 border-purple-500/20",
  request_clarification: "text-violet-500 bg-violet-500/5 border-violet-500/20",
  ...Object.fromEntries(
    scholarlyTools.map((tool) => [
      tool,
      "text-violet-500 bg-violet-500/5 border-violet-500/20",
    ]),
  ),
};

export default function ToolStatusBar({ toolStatus }) {
  const { t } = useTranslation(["pages", "common"]);
  if (!toolStatus) return null;
  const ToolIcon = toolIconMap[toolStatus.tool] || FileText;
  const colors =
    toolColorMap[toolStatus.tool] ||
    "text-emerald-500 bg-emerald-500/5 border-emerald-500/20";
  return (
    <div
      className={`flex items-center gap-2 text-xs text-muted-foreground py-1 px-2 rounded ${colors}`}
    >
      <ToolIcon className="size-3 animate-pulse" />
      <span>
        {toolStatus.tool === "web_search"
          ? t("pages:writingChat.toolStatus.searching", {
              query: toolStatus.args?.query || "...",
            })
          : t(`pages:writingChat.toolStatus.${toolStatus.label}`)}
      </span>
    </div>
  );
}
