import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import Check from "lucide-react/dist/esm/icons/check.mjs";
import Download from "lucide-react/dist/esm/icons/download.mjs";
import FileDiff from "lucide-react/dist/esm/icons/file-diff.mjs";
import FolderOpen from "lucide-react/dist/esm/icons/folder-open.mjs";
import Loader2 from "lucide-react/dist/esm/icons/loader-circle.mjs";
import Play from "lucide-react/dist/esm/icons/play.mjs";
import RefreshCw from "lucide-react/dist/esm/icons/refresh-cw.mjs";
import ScrollText from "lucide-react/dist/esm/icons/scroll-text.mjs";
import Square from "lucide-react/dist/esm/icons/square.mjs";
import Trash2 from "lucide-react/dist/esm/icons/trash-2.mjs";
import Undo2 from "lucide-react/dist/esm/icons/undo-2.mjs";
import Wand2 from "lucide-react/dist/esm/icons/wand-sparkles.mjs";
import Brain from "lucide-react/dist/esm/icons/brain.mjs";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import PageHeader from "@/components/PageHeader";
import ModelBadge from "@/components/ModelBadge";
import { api } from "@/api/index";
import { isMultiUser } from "@/api/connection";
import CodeBlock from "@/components/CodeBlock";
import CodeEditorInput from "@/components/CodeEditorInput";
import useIsOpened from "@/hooks/useIsOpened";
import useStyleModifyAgent from "@/hooks/useStyleModifyAgent";
import useCanWrite from "@/hooks/useCanWrite";

function compileLabel(t, status) {
  if (status === "passed") return t("pages:styleModify.compilePassed");
  if (status === "failed") return t("pages:styleModify.compileFailed");
  return t("pages:styleModify.compileNotRun");
}

export default function StyleModifyPage() {
  const { t } = useTranslation(["pages", "common"]);
  const agent = useStyleModifyAgent();
  // 模式感知"项目已打开"：多用户模式后端不返回 root_path（服务器路径不外露），
  // 不能以 rootPath 判定打开状态
  const isOpened = useIsOpened();
  // 多用户模式：viewer 只读，禁用 Agent 提案应用 / 撤销按钮（后端 403 兜底）
  const canWrite = useCanWrite();

  const [pasteLatex, setPasteLatex] = useState("");
  const [pasteDescription, setPasteDescription] = useState("");
  const [pasteLoading, setPasteLoading] = useState(false);
  const [pasteError, setPasteError] = useState("");
  const [result, setResult] = useState("");
  const [agentRequirement, setAgentRequirement] = useState("");
  const [refineMessage, setRefineMessage] = useState("");
  const [showTrace, setShowTrace] = useState(false);

  // 稳定引用（rerender-memo）：CodeEditorInput 是 memo 组件，内联 onChange
  // 会在 agent 流式更新引发的本页重渲染中使其失效
  const handlePasteLatexChange = useCallback(
    (e) => setPasteLatex(e.target.value),
    [],
  );

  const isAgent = agent.mode === "agent";

  const handlePasteModify = useCallback(async () => {
    if (!pasteLatex.trim() || !pasteDescription.trim()) return;
    setPasteLoading(true);
    setPasteError("");
    setResult("");
    try {
      const data = await api.styleModify.modify(pasteLatex, pasteDescription);
      setResult(data.latex);
    } catch (e) {
      setPasteError(e.message);
    } finally {
      setPasteLoading(false);
    }
  }, [pasteLatex, pasteDescription]);

  const handleCopy = useCallback(() => {
    if (result) navigator.clipboard.writeText(result).catch(() => {});
  }, [result]);

  const handleAgentRun = useCallback(() => {
    // 与运行按钮的禁用条件保持一致：已有 session 时不允许静默新建会话
    // 覆盖当前未审核的 proposal。
    if (agentRequirement.trim() && !agent.sessionId)
      agent.run(agentRequirement);
  }, [agentRequirement, agent.sessionId, agent.run]);

  const handleRefine = useCallback(() => {
    if (refineMessage.trim()) agent.refine(refineMessage);
  }, [refineMessage, agent.refine]);

  const handleKeyDown = useCallback(
    (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
        if (isAgent) handleAgentRun();
        else handlePasteModify();
      }
    },
    [isAgent, handleAgentRun, handlePasteModify],
  );

  const handleKeyDownRef = useRef(handleKeyDown);
  useEffect(() => {
    handleKeyDownRef.current = handleKeyDown;
  });
  useEffect(() => {
    const onKeyDown = (e) => handleKeyDownRef.current(e);
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const compileStatus =
    agent.compile?.status || agent.proposal?.compile?.status || "not_run";
  const compileLog = agent.compile?.log || agent.proposal?.compile?.log || "";
  const compileErrors =
    agent.compile?.errors || agent.proposal?.compile?.errors || [];
  const applied = agent.operation?.status === "applied";

  return (
    <div className="min-h-screen p-8 animate-tab-enter">
      <div className="max-w-6xl mx-auto">
        <PageHeader
          title={t("pages:styleModify.title")}

          showWorkspace
        >
          <ModelBadge role="agent" />
        </PageHeader>
      </div>

      <div className="max-w-6xl mx-auto space-y-6">
        <Tabs value={agent.mode} onValueChange={agent.setMode}>
          <TabsList className="mx-auto mb-6">
            <TabsTrigger value="agent">
              {t("pages:styleModify.modeAgent")}
            </TabsTrigger>
            <TabsTrigger value="paste">
              {t("pages:styleModify.modePaste")}
            </TabsTrigger>
          </TabsList>

          <TabsContent value="agent" className="animate-tab-enter">
            <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
              <div className="space-y-4">
                {!isOpened ? (
                  <Card>
                    <CardHeader className="pb-2">
                      <CardTitle className="text-sm">
                        {t("pages:styleModify.noProjectTitle")}
                      </CardTitle>
                      <CardDescription>
                        {t("pages:styleModify.noProjectDesc")}
                      </CardDescription>
                    </CardHeader>
                    <CardContent>
                      <Button
                        nativeButton={false}
                        render={
                          <Link
                            to={isMultiUser() ? "/projects" : "/workspace"}
                          />
                        }
                      >
                        <FolderOpen />
                        {isMultiUser()
                          ? t("common:openProjects")
                          : t("common:openWorkspace")}
                      </Button>
                    </CardContent>
                  </Card>
                ) : (
                  <Card>
                    <CardHeader className="pb-2">
                      <CardTitle className="text-sm">
                        {t("pages:styleModify.agentRequirement")}
                      </CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-3">
                      <Textarea
                        className="min-h-32 resize-none"
                        value={agentRequirement}
                        onChange={(e) => setAgentRequirement(e.target.value)}
                        placeholder={t(
                          "pages:styleModify.agentRequirementPlaceholder",
                        )}
                      />
                      <div className="flex flex-wrap items-center gap-2">
                        {agent.running ? (
                          <Button variant="secondary" onClick={agent.stop}>
                            <Square />
                            {t("pages:styleModify.agentStop")}
                          </Button>
                        ) : (
                          <Button
                            onClick={handleAgentRun}
                            disabled={
                              !agentRequirement.trim() || !!agent.sessionId
                            }
                          >
                            <Play />
                            {t("pages:styleModify.agentRun")}
                          </Button>
                        )}
                        {agent.sessionId && (
                          <Button
                            variant="outline"
                            onClick={() => agent.loadSession(agent.sessionId)}
                          >
                            <RefreshCw />
                            {t("pages:styleModify.refreshSession")}
                          </Button>
                        )}
                      </div>
                      {agent.error && (
                        <p className="text-sm text-destructive">
                          {agent.error}
                        </p>
                      )}
                      {agent.warning && (
                        <p className="text-sm text-amber-600">
                          {agent.warning}
                        </p>
                      )}
                    </CardContent>
                  </Card>
                )}

                <Card>
                  <CardHeader className="pb-2">
                    <div className="flex items-center justify-between">
                      <CardTitle className="text-sm">
                        {t("pages:styleModify.agentTimeline")}
                      </CardTitle>
                      {agent.sessionId && (
                        <div className="flex items-center gap-2">
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => setShowTrace((v) => !v)}
                          >
                            <ScrollText />
                            {t("pages:styleModify.viewTrace")}
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={agent.downloadTrace}
                          >
                            <Download />
                            {t("pages:styleModify.downloadTrace")}
                          </Button>
                        </div>
                      )}
                    </div>
                  </CardHeader>
                  <CardContent>
                    {(agent.running || agent.reasoningText) && (
                      <details
                        className="mb-2 rounded-md border bg-muted/40"
                        open
                      >
                        <summary className="cursor-pointer flex items-center gap-2 px-2.5 py-1.5 text-xs text-muted-foreground">
                          <Brain className="size-3.5" />
                          {agent.running
                            ? t("pages:styleModify.thinking")
                            : t("pages:styleModify.thoughtProcess")}
                          {agent.reasoningText && (
                            <span className="ml-auto font-mono text-[10px]">
                              {agent.reasoningText.length}
                            </span>
                          )}
                        </summary>
                        {agent.reasoningText && (
                          <div className="max-h-40 overflow-y-auto px-2.5 pb-2 font-mono text-[10px] whitespace-pre-wrap text-muted-foreground">
                            {agent.reasoningText}
                          </div>
                        )}
                      </details>
                    )}
                    {agent.steps.length === 0 ? (
                      <p className="text-sm text-muted-foreground">
                        {t("pages:styleModify.agentTimelineEmpty")}
                      </p>
                    ) : (
                      <ol className="space-y-2 text-sm">
                        {agent.steps.map((step) => (
                          <li
                            key={step._key}
                            className="rounded-md border bg-muted/30 p-2"
                          >
                            <div className="font-medium">
                              {t(
                                `pages:styleModify.step_${step.type || "tool_result"}`,
                              )}
                              {step.tool ? ` · ${step.tool}` : ""}
                            </div>
                            {step.summary && (
                              <div className="mt-1 text-xs text-muted-foreground">
                                {step.summary}
                              </div>
                            )}
                          </li>
                        ))}
                      </ol>
                    )}
                    {showTrace && (
                      <div className="mt-4">
                        <CodeBlock
                          code={
                            agent.trace
                              ? JSON.stringify(agent.trace, null, 2)
                              : t("pages:styleModify.traceEmpty")
                          }
                          language="json"
                          lineNumbers={false}
                          className="max-h-80 overflow-auto text-xs"
                        />
                        {!agent.trace && agent.sessionId && (
                          <Button
                            size="sm"
                            variant="outline"
                            className="mt-2"
                            onClick={agent.loadTrace}
                          >
                            {agent.loadingTrace
                              ? t("common:loading")
                              : t("pages:styleModify.viewTrace")}
                          </Button>
                        )}
                      </div>
                    )}
                  </CardContent>
                </Card>
              </div>

              <div className="space-y-4">
                <Card>
                  <CardHeader className="pb-2">
                    <CardTitle className="text-sm">
                      {t("pages:styleModify.proposalTitle")}
                    </CardTitle>
                    <CardDescription>
                      {agent.proposal?.summary || agent.summary || " "}
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-medium">
                        {t("pages:styleModify.compileStatus")}
                      </span>
                      <span
                        className={`rounded-md px-2 py-0.5 text-xs font-medium ${
                          compileStatus === "passed"
                            ? "bg-emerald-500/10 text-emerald-600"
                            : compileStatus === "failed"
                              ? "bg-red-500/10 text-red-600"
                              : "bg-muted text-muted-foreground"
                        }`}
                      >
                        {compileLabel(t, compileStatus)}
                      </span>
                    </div>
                    {compileLog && (
                      <details>
                        <summary className="cursor-pointer text-xs text-muted-foreground">
                          {t("pages:styleModify.viewCompileLog")}
                        </summary>
                        <CodeBlock
                          code={compileLog}
                          language="plaintext"
                          lineNumbers={false}
                          className="mt-2 max-h-72 overflow-auto text-xs"
                        />
                      </details>
                    )}
                    {compileErrors.length > 0 && (
                      <ul className="space-y-1 text-xs text-destructive">
                        {compileErrors.map((err, idx) => (
                          <li key={idx}>
                            {err.type}
                            {err.file ? ` · ${err.file}` : ""}
                            {err.line ? `:${err.line}` : ""}
                          </li>
                        ))}
                      </ul>
                    )}

                    {agent.files.length === 0 ? (
                      <p className="text-sm text-muted-foreground">
                        {t("pages:styleModify.noFiles")}
                      </p>
                    ) : (
                      <Tabs
                        key={agent.files.map((f) => f.path).join(",")}
                        defaultValue={agent.files[0]?.path}
                      >
                        <TabsList className="max-w-full overflow-x-auto">
                          {agent.files.map((file) => (
                            <TabsTrigger key={file.path} value={file.path}>
                              {file.path}
                            </TabsTrigger>
                          ))}
                        </TabsList>
                        {agent.files.map((file) => (
                          <TabsContent
                            key={file.path}
                            value={file.path}
                            className="animate-tab-enter"
                          >
                            <CodeBlock
                              code={file.diff || t("common:noChanges")}
                              language="plaintext"
                              lineNumbers={false}
                              className="max-h-[28rem] overflow-auto text-xs"
                            />
                          </TabsContent>
                        ))}
                      </Tabs>
                    )}

                    <div className="flex flex-wrap items-center gap-2 pt-1">
                      <Button
                        onClick={agent.apply}
                        disabled={
                          !canWrite ||
                          !agent.proposal?.applicable ||
                          agent.applying ||
                          agent.running
                        }
                      >
                        {agent.applying ? (
                          <Loader2 className="animate-spin" />
                        ) : (
                          <Check />
                        )}
                        {agent.applying
                          ? t("pages:styleModify.applyingProposal")
                          : t("pages:styleModify.applyProposal")}
                      </Button>
                      {applied && (
                        <Button
                          variant="outline"
                          onClick={agent.undo}
                          disabled={!canWrite || agent.applying}
                        >
                          <Undo2 />
                          {t("pages:styleModify.undoOperation")}
                        </Button>
                      )}
                      {agent.sessionId && (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={agent.deleteSession}
                        >
                          <Trash2 />
                          {t("pages:styleModify.deleteSession")}
                        </Button>
                      )}
                    </div>
                  </CardContent>
                </Card>

                <Card>
                  <CardHeader className="pb-2">
                    <CardTitle className="text-sm">
                      {t("pages:styleModify.refineTitle")}
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    <Textarea
                      className="min-h-24 resize-none"
                      value={refineMessage}
                      onChange={(e) => setRefineMessage(e.target.value)}
                      placeholder={t("pages:styleModify.refinePlaceholder")}
                    />
                    <Button
                      onClick={handleRefine}
                      disabled={
                        !refineMessage.trim() ||
                        !agent.sessionId ||
                        !agent.proposal ||
                        agent.running
                      }
                    >
                      <Wand2 />
                      {agent.running
                        ? t("pages:styleModify.refining")
                        : t("pages:styleModify.refineBtn")}
                    </Button>
                  </CardContent>
                </Card>
              </div>
            </div>
          </TabsContent>

          <TabsContent value="paste" className="animate-tab-enter">
            <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
              <div className="space-y-4">
                <Card>
                  <CardHeader className="pb-2">
                    <CardTitle className="text-sm">
                      {t("pages:styleModify.sourceCode")}
                    </CardTitle>
                    <CardDescription>
                      {t("pages:styleModify.sourcePlaceholder")}
                    </CardDescription>
                  </CardHeader>
                  <CardContent>
                    <CodeEditorInput
                      value={pasteLatex}
                      onChange={handlePasteLatexChange}
                      placeholder={t("pages:styleModify.sourcePlaceholder")}
                      className="min-h-80"
                    />
                  </CardContent>
                </Card>
              </div>

              <div className="space-y-4">
                <Card>
                  <CardHeader className="pb-2">
                    <CardTitle className="text-sm">
                      {t("pages:styleModify.requirement")}
                    </CardTitle>
                    <CardDescription>
                      {t("pages:styleModify.requirementPlaceholder")}
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    <Textarea
                      className="min-h-32 resize-none"
                      value={pasteDescription}
                      onChange={(e) => setPasteDescription(e.target.value)}
                      placeholder={t("pages:styleModify.requirementExamples")}
                    />
                    <div className="flex items-center gap-2">
                      <Button
                        onClick={handlePasteModify}
                        disabled={
                          pasteLoading ||
                          !pasteLatex.trim() ||
                          !pasteDescription.trim()
                        }
                      >
                        {pasteLoading ? (
                          <Loader2 className="animate-spin" />
                        ) : (
                          <Wand2 />
                        )}
                        {pasteLoading
                          ? t("pages:styleModify.modifyBtnLoading")
                          : t("pages:styleModify.modifyBtn")}
                      </Button>
                      <span className="text-xs text-muted-foreground">
                        {t("pages:styleModify.submitHint")}
                      </span>
                    </div>
                    {pasteError && (
                      <p className="text-sm text-destructive">{pasteError}</p>
                    )}
                  </CardContent>
                </Card>

                {result && (
                  <Card>
                    <CardHeader className="pb-2">
                      <div className="flex items-center justify-between">
                        <CardTitle className="text-sm">
                          {t("pages:styleModify.resultTitle")}
                        </CardTitle>
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={handleCopy}
                        >
                          <FileDiff />
                          {t("common:copy")}
                        </Button>
                      </div>
                    </CardHeader>
                    <CardContent>
                      <CodeBlock
                        code={result}
                        language="latex"
                        className="p-4 max-h-96 text-sm"
                      />
                    </CardContent>
                  </Card>
                )}
              </div>
            </div>
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
}
