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
  /** 在途编译序号：丢弃过期请求的迟到响应，防止旧结果覆盖新状态 */
  const compileSeqRef = useRef(0);

  const resolveCompileTarget = useCallback(() => {
    if (mainFile) return mainFile;
    const cur = useEditorStore.getState().filePath;
    if (cur && cur.endsWith(".tex")) return cur;
    return null;
  }, [mainFile]);

  const handleCompile = useCallback(async () => {
    // 廉价同步判断前置于网络 await：无编译目标时直接提示，不白跑
    // 一次保存往返（async-cheap-condition-before-await）
    const target = resolveCompileTarget();
    if (!target) {
      toast.error(t("pages:editor.compileNoMainFile"));
      return;
    }
    const seq = ++compileSeqRef.current;
    // 脏改动先落盘：doSave 失败（含 409 → ConflictDialog 接管）即中断编译
    if (useEditorStore.getState().dirty) {
      const saved = await saveRef.current();
      if (!saved || seq !== compileSeqRef.current) return;
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
      if (seq !== compileSeqRef.current) return; // 已被更新的编译接管
      setCompileResult(data);
      if (data.success) {
        const url = await api.compile.pdfBlobUrl(target, workspaceId);
        if (seq !== compileSeqRef.current) {
          URL.revokeObjectURL(url); // 过期响应自回收刚取得的 blob URL
          return;
        }
        pdfUrlRef.current = url;
        setPdfUrl(url);
        onCompileSuccess(data);
      } else {
        onCompileError(data);
      }
    } catch (e) {
      if (seq !== compileSeqRef.current) return;
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
      // 仅当前编译有权收尾；被接管的在途请求不碰状态（新请求已重新置态）
      if (seq === compileSeqRef.current) {
        setCompiling(false);
      }
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
