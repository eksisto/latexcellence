import { useEffect } from "react";
import { fetchConfigStatus } from "@/hooks/useConfigStatus";
import useStore from "@/store/useStore";

const ALL_ENABLED = {
  workspace: true,
  formula_ocr: true,
  table_ocr: true,
  template_gen: true,
  template_library: true,
  style_modify: true,
  typo_check: true,
  expression_opt: true,
  code_review: true,
  compile: true,
  git_diff: true,
  git_manage: true,
  format_converter: true,
  terminology_check: true,
  paragraph_translate: true,
  writing_chat: true,
  bib_manager: true,
  plot_gen: true,
  editor: true,
  editor_assistant: true,
  project_tools: true,
  web_search: true,
  scholarly_research: true,
};

export default function useEnabledFeatures() {
  const enabledFeatures = useStore((s) => s.enabledFeatures);
  const setEnabledFeatures = useStore((s) => s.setEnabledFeatures);

  useEffect(() => {
    if (enabledFeatures !== null) return;
    let mounted = true;
    // 走共享探测层：与 useAuth 启动探测 / UploadProjectDialog 等消费方
    // 共享 in-flight 去重与 TTL 缓存，不再各页独立重复请求 /config/status
    fetchConfigStatus()
      .then(({ features }) => {
        if (mounted && features) {
          setEnabledFeatures({ workspace: true, ...features });
        }
      })
      .catch(() => {
        if (mounted) setEnabledFeatures(ALL_ENABLED);
      });
    return () => {
      mounted = false;
    };
  }, [enabledFeatures, setEnabledFeatures]);

  return enabledFeatures;
}
