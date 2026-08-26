import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import useEditorStore from "@/store/editorStore";
import { api } from "@/api/index";
import { translateError } from "@/utils/errorMessages";

/**
 * 编译联动：编译状态 / PDF blob ref 回收 / handleCompile 编排。
 * 从 EditorPage 拆出，与 useEditorBuffer 通过 saveRef 解耦。
 *
 * @param {{ saveRef, mainFile, t, workspaceId, setPanelCollapsed, setPanelTab, onCompileSuccess, onCompileError }} params
 */
export default function useEditorCompile({
  saveRef,
  mainFile,
  t,
  workspaceId,
  setPanelCollapsed,
  setPanelTab,
  onCompileSuccess,
  onCompileError,
}) {
  const [compiling, setCompiling] = useState(false);
  const [compileResult, setCompileResult] = useState(null);
  const [pdfUrl, setPdfUrl] = useState(null);
  const pdfUrlRef = useRef(null);

  const resolveCompileTarget = useCallback(() => {
    if (mainFile) return mainFile;
    const cur = useEditorStore.getState().filePath;
    if (cur && cur.endsWith(".tex")) return cur;
    return null;
  }, [mainFile]);

  const handleCompile = useCallback(async () => {
    // 脏改动先落盘：doSave 失败（含 409 → ConflictDialog 接管）即中断编译
    if (useEditorStore.getState().dirty) {
      const saved = await saveRef.current();
      if (!saved) return;
    }
    const target = resolveCompileTarget();
    if (!target) {
      toast.error(t("pages:editor.compileNoMainFile"));
      return;
    }
    // 展开面板 + 切换到编译 Tab + 标记编译中
    setPanelCollapsed(false);
    setPanelTab("compile");
    setCompiling(true);
    setCompileResult(null);
    // 直接经 ref 回收旧 URL
    if (pdfUrlRef.current) {
      URL.revokeObjectURL(pdfUrlRef.current);
      pdfUrlRef.current = null;
    }
    setPdfUrl(null);
    try {
      const data = await api.compile.run(target, workspaceId);
      setCompileResult(data);
      if (data.success) {
        const url = await api.compile.pdfBlobUrl(target, workspaceId);
        pdfUrlRef.current = url;
        setPdfUrl(url);
        onCompileSuccess(data);
      } else {
        onCompileError(data);
      }
    } catch (e) {
      const msg = translateError({ error: e.code, message: e.message });
      setCompileResult({
        success: false,
        explanation: msg,
        fixes: [],
        stdout: "",
        stderr: "",
      });
      onCompileError({ explanation: msg });
    } finally {
      setCompiling(false);
    }
  }, [
    resolveCompileTarget,
    t,
    workspaceId,
    saveRef,
    setPanelCollapsed,
    setPanelTab,
    onCompileSuccess,
    onCompileError,
  ]);

  // 卸载时回收 PDF blob URL
  useEffect(() => {
    return () => {
      if (pdfUrlRef.current) {
        URL.revokeObjectURL(pdfUrlRef.current);
        pdfUrlRef.current = null;
      }
    };
  }, []);

  return {
    compiling,
    compileResult,
    pdfUrl,
    handleCompile,
  };
}
