import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import PageHeader from "@/components/PageHeader";
import EmptyState from "@/components/EmptyState";
import TemplateCard from "@/components/template-center/TemplateCard";
import TemplateDetailDialog from "@/components/template-center/TemplateDetailDialog";
import TemplateUploadDialog from "@/components/template-center/TemplateUploadDialog";
import TemplateEditDialog from "@/components/template-center/TemplateEditDialog";
import CreateProjectDialog from "@/components/template-center/CreateProjectDialog";
import ConfirmDialog from "@/components/ConfirmDialog";
import { api } from "@/api/index";
import useStore from "@/store/useStore";
import useEnabledFeatures from "@/hooks/useEnabledFeatures";
import { translateError } from "@/utils/errorMessages";
import {
  TEMPLATE_CATEGORIES,
  filterTemplates,
  sortTemplates,
} from "@/utils/templateCenter";
import LayoutTemplate from "lucide-react/dist/esm/icons/layout-template.mjs";
import UploadCloud from "lucide-react/dist/esm/icons/upload-cloud.mjs";
import Search from "lucide-react/dist/esm/icons/search.mjs";
import Loader2 from "lucide-react/dist/esm/icons/loader-circle.mjs";
import ShieldOff from "lucide-react/dist/esm/icons/shield-off.mjs";

/**
 * 模板中心（/templates）：搜索 + 分类筛选 + 「内置模板/我的模板」两个 tab +
 * 响应式卡片网格。内置模板卡片带缩略图与标识；「我的模板」支持上传、
 * 编辑（改名/分类）与删除。从模板创建项目后按模式打开进工作区。
 */
export default function TemplateCenterPage() {
  const { t } = useTranslation(["pages", "common"]);
  const navigate = useNavigate();
  const enabledFeatures = useEnabledFeatures();

  const [templates, setTemplates] = useState([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState(null);
  const [keyword, setKeyword] = useState("");
  const [category, setCategory] = useState("");
  const [tab, setTab] = useState("builtin");

  // 对话框状态：详情 / 上传 / 编辑 / 创建项目 / 删除确认
  const [detailTemplate, setDetailTemplate] = useState(null);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [editTemplate, setEditTemplate] = useState(null);
  const [createTemplate, setCreateTemplate] = useState(null);
  const [deleteTemplate, setDeleteTemplate] = useState(null);
  const [deleting, setDeleting] = useState(false);

  // 功能关闭：入口全部隐藏（页面自身展示空态；后端端点此时也已关闭）
  const featureEnabled = !enabledFeatures || !!enabledFeatures.template_library;
  // 功能开关是否已水合（configStatus 回包或失败降级后非 null）
  const featuresResolved = enabledFeatures !== null;

  const loadTemplates = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const data = await api.templateLibrary.list();
      setTemplates(data.templates || []);
    } catch (e) {
      setLoadError(translateError({ error: e.code, message: e.message }));
    } finally {
      setLoading(false);
    }
  }, []);

  // 等开关水合后再拉列表：部署关闭 template_library 时避免先打到已关闭的
  // /api/templates（404）出现错误态闪烁，随后才切入「功能未开启」空态
  useEffect(() => {
    if (featuresResolved && featureEnabled) loadTemplates();
  }, [featuresResolved, featureEnabled, loadTemplates]);

  /** 按 tab（内置/我的）+ 搜索关键字 + 分类过滤并排序 */
  const visibleTemplates = useMemo(() => {
    const kind = tab === "builtin" ? "builtin" : "user";
    return sortTemplates(
      filterTemplates(templates, { keyword, category }),
    ).filter((tpl) => tpl.kind === kind);
  }, [templates, tab, keyword, category]);

  /** 创建成功：多用户对齐 ProjectWorkbenchPage.handleOpen（清空项目态 →
   * 置目标项目身份 → 进 /workspace，由工作台恢复链路完成打开，含主文档
   * 段落拉取与角色同步，与从工作台打开项目行为一致）；单用户清空当前
   * 项目状态后以新路径触发 WorkspacePage 既有恢复链路（含主文档段落拉取） */
  const handleProjectCreated = useCallback(
    (project) => {
      const store = useStore.getState();
      if (project?.project_id) {
        store.clearProject();
        store.setOpenedProject({
          projectId: project.project_id,
          // 角色未知先置 null，工作台打开时经项目列表同步
          projectRole: null,
        });
        toast.success(
          t("pages:templateCenter.create.successToast", {
            name: project.name || "",
          }),
        );
        navigate("/workspace");
        return;
      }
      if (project?.path) {
        store.setRootPath(null);
        store.setProjectPath(project.path);
        store.setProjectId(null);
        store.setProjectRole(null);
        store.setFileTree([]);
        store.setMainFile(null);
        store.setActiveFile(null);
        toast.success(
          t("pages:templateCenter.create.successToast", {
            name: project.name || "",
          }),
        );
        navigate("/workspace");
      }
    },
    [navigate, t],
  );

  const handleDelete = useCallback(async () => {
    if (!deleteTemplate || deleting) return;
    setDeleting(true);
    try {
      await api.templateLibrary.delete(deleteTemplate.id);
      setTemplates((prev) =>
        prev.filter((tpl) => tpl.id !== deleteTemplate.id),
      );
      setDeleteTemplate(null);
      toast.success(t("pages:templateCenter.deleteSuccess"));
    } catch (e) {
      toast.error(t("pages:templateCenter.deleteFailed"), {
        description: translateError({ error: e.code, message: e.message }),
      });
    } finally {
      setDeleting(false);
    }
  }, [deleteTemplate, deleting, t]);

  if (!featureEnabled) {
    return (
      <div className="min-h-screen p-8 animate-tab-enter">
        <div className="mx-auto max-w-6xl">
          <PageHeader
            title={t("pages:templateCenter.title")}
            description={t("pages:templateCenter.description")}
          />
          <EmptyState
            icon={ShieldOff}
            title={t("pages:templateCenter.disabledTitle")}
            description={t("pages:templateCenter.disabledDescription")}
          />
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen p-8 animate-tab-enter">
      <div className="mx-auto max-w-6xl">
        <PageHeader
          title={t("pages:templateCenter.title")}
          description={t("pages:templateCenter.description")}
        >
          <Button onClick={() => setUploadOpen(true)}>
            <UploadCloud /> {t("pages:templateCenter.uploadBtn")}
          </Button>
        </PageHeader>

        {/* 搜索 + 分类 chip */}
        <div className="mb-4 space-y-3">
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              className="pl-8"
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
              placeholder={t("pages:templateCenter.searchPlaceholder")}
            />
          </div>
          <div className="flex flex-wrap gap-1.5">
            <Button
              variant={category === "" ? "default" : "outline"}
              size="sm"
              className="h-7 rounded-full px-3 text-xs"
              onClick={() => setCategory("")}
            >
              {t("pages:templateCenter.allCategories")}
            </Button>
            {TEMPLATE_CATEGORIES.map((c) => (
              <Button
                key={c}
                variant={category === c ? "default" : "outline"}
                size="sm"
                className="h-7 rounded-full px-3 text-xs"
                onClick={() => setCategory(category === c ? "" : c)}
              >
                {t(`pages:templateCenter.categories.${c}`)}
              </Button>
            ))}
          </div>
        </div>

        <Tabs value={tab} onValueChange={(v) => setTab(v)} className="gap-4">
          <TabsList>
            <TabsTrigger value="builtin">
              {t("pages:templateCenter.tabBuiltin")}
            </TabsTrigger>
            <TabsTrigger value="mine">
              {t("pages:templateCenter.tabMine")}
            </TabsTrigger>
          </TabsList>

          {["builtin", "mine"].map((tabKey) => (
            <TabsContent key={tabKey} value={tabKey} className="mt-0">
              {loading ? (
                <div className="flex items-center justify-center gap-2 py-16 text-sm text-muted-foreground">
                  <Loader2 className="size-4 animate-spin" />
                  {t("common:loading")}
                </div>
              ) : loadError ? (
                <div className="space-y-3 py-10 text-center">
                  <p className="text-sm text-destructive">{loadError}</p>
                  <Button variant="outline" size="sm" onClick={loadTemplates}>
                    {t("pages:templateCenter.retry")}
                  </Button>
                </div>
              ) : visibleTemplates.length === 0 ? (
                <EmptyState
                  icon={LayoutTemplate}
                  title={
                    tabKey === "mine"
                      ? t("pages:templateCenter.emptyMineTitle")
                      : t("pages:templateCenter.emptyTitle")
                  }
                  description={
                    tabKey === "mine"
                      ? t("pages:templateCenter.emptyMineDescription")
                      : t("pages:templateCenter.emptyDescription")
                  }
                >
                  {tabKey === "mine" ? (
                    <Button size="sm" onClick={() => setUploadOpen(true)}>
                      <UploadCloud />
                      {t("pages:templateCenter.uploadBtn")}
                    </Button>
                  ) : null}
                </EmptyState>
              ) : (
                <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                  {visibleTemplates.map((tpl) => (
                    <TemplateCard
                      key={tpl.id}
                      template={tpl}
                      onOpen={() => setDetailTemplate(tpl)}
                      onEdit={
                        tabKey === "mine"
                          ? () => setEditTemplate(tpl)
                          : undefined
                      }
                      onDelete={
                        tabKey === "mine"
                          ? () => setDeleteTemplate(tpl)
                          : undefined
                      }
                    />
                  ))}
                </div>
              )}
            </TabsContent>
          ))}
        </Tabs>
      </div>

      <TemplateDetailDialog
        template={detailTemplate}
        open={!!detailTemplate}
        onOpenChange={(open) => !open && setDetailTemplate(null)}
        onUse={(tpl) => {
          setDetailTemplate(null);
          setCreateTemplate(tpl);
        }}
      />
      <TemplateUploadDialog
        open={uploadOpen}
        onOpenChange={setUploadOpen}
        onUploaded={(tpl) => {
          if (tpl) {
            setTemplates((prev) => [...prev, tpl]);
            setTab("mine");
          }
          toast.success(t("pages:templateCenter.upload.successToast"));
        }}
      />
      <TemplateEditDialog
        template={editTemplate}
        onOpenChange={(open) => !open && setEditTemplate(null)}
        onSaved={(tpl) => {
          if (tpl) {
            setTemplates((prev) =>
              prev.map((item) => (item.id === tpl.id ? tpl : item)),
            );
          }
          toast.success(t("pages:templateCenter.edit.savedToast"));
        }}
      />
      <CreateProjectDialog
        template={createTemplate}
        onOpenChange={(open) => !open && setCreateTemplate(null)}
        onCreated={handleProjectCreated}
      />
      <ConfirmDialog
        open={!!deleteTemplate}
        onOpenChange={(open) => !open && setDeleteTemplate(null)}
        title={t("pages:templateCenter.deleteConfirmTitle", {
          name: deleteTemplate?.name || "",
        })}
        description={t("pages:templateCenter.deleteConfirmDesc")}
        confirmText={t("common:delete")}
        variant="destructive"
        confirmLoading={deleting}
        onConfirm={handleDelete}
      />
    </div>
  );
}
