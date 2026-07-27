import { useEffect } from "react";
import { api } from "@/api/index";
import useStore from "@/store/useStore";

const ALL_ENABLED = {
  workspace: true,
  formula_ocr: true,
  table_ocr: true,
  template_gen: true,
  style_modify: true,
  typo_check: true,
  expression_opt: true,
  code_review: true,
  compile: true,
  git_diff: true,
  format_converter: true,
  terminology_check: true,
  paragraph_translate: true,
  writing_chat: true,
  bib_manager: true,
  web_search: true,
};

export default function useEnabledFeatures() {
  const enabledFeatures = useStore((s) => s.enabledFeatures);
  const setEnabledFeatures = useStore((s) => s.setEnabledFeatures);

  useEffect(() => {
    if (enabledFeatures !== null) return;
    let mounted = true;
    api
      .configStatus()
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
