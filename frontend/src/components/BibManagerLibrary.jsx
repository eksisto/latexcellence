import {
  useState,
  useCallback,
  useEffect,
  useMemo,
  useDeferredValue,
  useRef,
} from "react";
import { toast } from "sonner";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import Library from "lucide-react/dist/esm/icons/library.mjs";
import Loader2 from "lucide-react/dist/esm/icons/loader-circle.mjs";
import Search from "lucide-react/dist/esm/icons/search.mjs";
import Trash2 from "lucide-react/dist/esm/icons/trash-2.mjs";
import Edit3 from "lucide-react/dist/esm/icons/pen-line.mjs";
import AlertTriangle from "lucide-react/dist/esm/icons/triangle-alert.mjs";
import CheckCircle from "lucide-react/dist/esm/icons/circle-check-big.mjs";
import Filter from "lucide-react/dist/esm/icons/funnel.mjs";
import ConfirmDialog from "@/components/ConfirmDialog";
import UndoRedoButtons from "@/components/UndoRedoButtons";
import { api } from "@/api/index";
import useStore from "@/store/useStore";
import useCanWrite from "@/hooks/useCanWrite";

export default function BibManagerLibrary() {
  const { t } = useTranslation(["pages", "common"]);
  const workspaceId = useStore((s) => s.workspaceId);
  // 多用户模式写权限：viewer 隐藏撤销/重做与条目编辑/删除入口（后端 403 兜底）
  const canWrite = useCanWrite();

  const [bibFiles, setBibFiles] = useState([]);
  const [selectedBibFile, setSelectedBibFile] = useState("");
  const [entries, setEntries] = useState([]);
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(false);
  const [searchText, setSearchText] = useState("");
  const [typeFilter, setTypeFilter] = useState("all");
  const [yearFilter, setYearFilter] = useState(null);

  const [editEntry, setEditEntry] = useState(null);
  const [editBibtex, setEditBibtex] = useState("");
  const [savingEdit, setSavingEdit] = useState(false);

  const [deleteTarget, setDeleteTarget] = useState(null);
  const [deleting, setDeleting] = useState(false);

  const [duplicates, setDuplicates] = useState([]);
  const [dupTotal, setDupTotal] = useState(0);
  const [dupChecking, setDupChecking] = useState(false);
  const [showDupDialog, setShowDupDialog] = useState(false);
  const [deletingDupKey, setDeletingDupKey] = useState("");

  /** 条目加载序号：快速切换 .bib 文件时丢弃过期响应，防上一个文件的
   * 条目和统计写进当前视图（撤销/重做的重载与切换共用同一守卫） */
  const loadSeqRef = useRef(0);

  useEffect(() => {
    api.bibManager
      .listBibFiles(workspaceId)
      .then((data) => {
        setBibFiles(data.bib_files || []);
        if (data.bib_files?.length > 0 && !selectedBibFile) {
          setSelectedBibFile(data.bib_files[0]);
        }
      })
      .catch(() => {});
  }, [workspaceId]);

  const loadData = useCallback(
    async (filePath) => {
      if (!filePath) return;
      const seq = ++loadSeqRef.current;
      setLoading(true);
      try {
        const [entriesRes, statsRes] = await Promise.all([
          api.bibManager.parseEntries(filePath, workspaceId),
          api.bibManager.entryStats(filePath, workspaceId),
        ]);
        if (seq !== loadSeqRef.current) return;
        setEntries(entriesRes.entries || []);
        setStats(statsRes);
      } catch (e) {
        if (seq !== loadSeqRef.current) return;
        toast.error(t("pages:bibManager.library.loadFailed"), {
          description: e.message,
        });
      } finally {
        // 仅最新一次加载有权复位 loading，过期请求提前返回不灭新请求的 loading
        if (seq === loadSeqRef.current) setLoading(false);
      }
    },
    [workspaceId, t],
  );

  const handleAfterUndoRedo = useCallback(
    () => loadData(selectedBibFile),
    [selectedBibFile, loadData],
  );

  useEffect(() => {
    if (selectedBibFile) {
      loadData(selectedBibFile);
    }
  }, [selectedBibFile, loadData]);

  const handleFileChange = useCallback((file) => {
    setSelectedBibFile(file);
    setSearchText("");
    setTypeFilter("all");
    setYearFilter(null);
    setDuplicates([]);
  }, []);

  const handleDetectDuplicates = useCallback(async () => {
    if (!selectedBibFile) return;
    setDupChecking(true);
    try {
      const data = await api.bibManager.detectDuplicates(
        selectedBibFile,
        workspaceId,
      );
      setDuplicates(data.duplicates || []);
      setDupTotal(data.total_duplicates);
      setShowDupDialog(true);
      if (data.total_duplicates === 0) {
        toast.success(t("pages:bibManager.library.noDuplicates"));
      } else {
        toast.warning(
          t("pages:bibManager.library.duplicateFound", {
            count: data.total_duplicates,
          }),
        );
      }
    } catch (e) {
      toast.error(t("pages:bibManager.library.detectFailed"), {
        description: e.message,
      });
    } finally {
      setDupChecking(false);
    }
  }, [selectedBibFile, workspaceId, t]);

  const handleStartEdit = useCallback((entry) => {
    setEditEntry(entry);
    setEditBibtex(entry.raw_bibtex || "");
  }, []);

  const handleSaveEdit = useCallback(async () => {
    if (!editEntry || !editBibtex.trim()) return;
    setSavingEdit(true);
    try {
      await api.bibManager.updateEntry(
        selectedBibFile,
        editEntry.cite_key,
        editBibtex,
        workspaceId,
      );
      toast.success(t("pages:bibManager.library.editSuccess"));
      setEditEntry(null);
      setEditBibtex("");
      loadData(selectedBibFile);
    } catch (e) {
      toast.error(t("pages:bibManager.library.editFailed"), {
        description: e.message,
      });
    } finally {
      setSavingEdit(false);
    }
  }, [editEntry, editBibtex, selectedBibFile, workspaceId, t, loadData]);

  const handleDeleteConfirm = useCallback(async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await api.bibManager.deleteEntry(
        selectedBibFile,
        deleteTarget.cite_key,
        workspaceId,
      );
      toast.success(t("pages:bibManager.library.deleteSuccess"));
      setDeleteTarget(null);
      loadData(selectedBibFile);
    } catch (e) {
      toast.error(t("pages:bibManager.library.deleteFailed"), {
        description: e.message,
      });
    } finally {
      setDeleting(false);
    }
  }, [deleteTarget, selectedBibFile, workspaceId, t, loadData]);

  const handleDeleteFromDuplicates = useCallback(
    async (citeKey) => {
      setDeletingDupKey(citeKey);
      try {
        await api.bibManager.deleteEntry(selectedBibFile, citeKey, workspaceId);
        toast.success(t("pages:bibManager.library.deleteSuccess"));
        loadData(selectedBibFile);
        const data = await api.bibManager.detectDuplicates(
          selectedBibFile,
          workspaceId,
        );
        setDuplicates(data.duplicates || []);
        setDupTotal(data.total_duplicates);
        if (data.total_duplicates === 0) {
          setShowDupDialog(false);
        }
      } catch (e) {
        toast.error(t("pages:bibManager.library.deleteFailed"), {
          description: e.message,
        });
      } finally {
        setDeletingDupKey("");
      }
    },
    [selectedBibFile, workspaceId, t, loadData],
  );

  // 搜索过滤可能遍历大量文献条目，延迟值保证输入框优先响应
  const deferredSearchText = useDeferredValue(searchText);

  const filteredEntries = useMemo(() => {
    return entries.filter((entry) => {
      if (typeFilter !== "all" && entry.entry_type !== typeFilter) return false;
      if (
        yearFilter &&
        !(entry.fields?.year || "").trim().startsWith(yearFilter)
      )
        return false;
      if (!deferredSearchText.trim()) return true;
      const q = deferredSearchText.toLowerCase();
      const fields = entry.fields || {};
      return (
        entry.cite_key.toLowerCase().includes(q) ||
        (fields.author || "").toLowerCase().includes(q) ||
        (fields.title || "").toLowerCase().includes(q) ||
        (fields.year || "").includes(q) ||
        (fields.journal || "").toLowerCase().includes(q)
      );
    });
  }, [entries, typeFilter, yearFilter, deferredSearchText]);

  const allTypes = useMemo(
    () => [...new Set(entries.map((e) => e.entry_type).filter(Boolean))].sort(),
    [entries],
  );

  const sortedYears = useMemo(
    () =>
      stats
        ? Object.keys(stats.by_year)
            .filter((y) => y !== "unknown")
            .sort((a, b) => b.localeCompare(a))
        : [],
    [stats],
  );

  if (bibFiles.length === 0) {
    return (
      <Card>
        <CardContent className="py-12 text-center text-muted-foreground">
          <Library className="size-12 mx-auto mb-3 opacity-30" />
          <p>{t("pages:bibManager.step4NoBibFiles")}</p>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-6">
      {/* File selector */}
      {bibFiles.length > 1 && (
        <div className="mb-4">
          <Label className="text-xs">
            {t("pages:bibManager.step4SelectBibFile")}
          </Label>
          <Select value={selectedBibFile} onValueChange={handleFileChange}>
            <SelectTrigger className="min-w-48">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {bibFiles.map((f) => (
                <SelectItem key={f} value={f}>
                  {f}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center py-20">
          <Loader2 className="size-8 animate-spin text-muted-foreground" />
        </div>
      ) : (
        <>
          {/* Stats panel */}
          {stats && (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
              <Card>
                <CardHeader className="py-3 px-4">
                  <CardDescription className="text-xs">
                    {t("pages:bibManager.library.statsTotal")}
                  </CardDescription>
                  <CardTitle className="text-2xl">{stats.total}</CardTitle>
                </CardHeader>
              </Card>
              <Card>
                <CardHeader className="py-3 px-4">
                  <CardDescription className="text-xs">
                    {t("pages:bibManager.library.statsWithDoi")}
                  </CardDescription>
                  <CardTitle className="text-2xl">{stats.with_doi}</CardTitle>
                </CardHeader>
              </Card>
              <Card>
                <CardHeader className="py-3 px-4">
                  <CardDescription className="text-xs">
                    {t("pages:bibManager.library.statsTypes")}
                  </CardDescription>
                  <CardTitle className="text-2xl">
                    {Object.keys(stats.by_type).length}
                  </CardTitle>
                </CardHeader>
              </Card>
              <Card>
                <CardHeader className="py-3 px-4">
                  <CardDescription className="text-xs">
                    {t("pages:bibManager.library.statsRecent")}
                  </CardDescription>
                  <CardTitle className="text-2xl">
                    {sortedYears.length > 0 ? sortedYears[0] : "-"}
                  </CardTitle>
                </CardHeader>
              </Card>
            </div>
          )}

          {/* Type & year distribution */}
          {stats &&
            (Object.keys(stats.by_type).length > 1 ||
              Object.keys(stats.by_year).length > 1) && (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
                {Object.keys(stats.by_type).length > 1 && (
                  <Card>
                    <CardHeader className="pb-2">
                      <CardTitle className="text-sm">
                        {t("pages:bibManager.library.typeDistribution")}
                      </CardTitle>
                    </CardHeader>
                    <CardContent>
                      <div className="flex flex-wrap gap-2">
                        {Object.entries(stats.by_type)
                          .sort(([, a], [, b]) => b - a)
                          .map(([type, count]) => (
                            <Badge
                              key={type}
                              variant="secondary"
                              className="text-xs"
                            >
                              {type} ({count})
                            </Badge>
                          ))}
                      </div>
                    </CardContent>
                  </Card>
                )}
                {Object.keys(stats.by_year).length > 1 && (
                  <Card>
                    <CardHeader className="pb-2">
                      <CardTitle className="text-sm">
                        {t("pages:bibManager.library.yearDistribution")}
                      </CardTitle>
                    </CardHeader>
                    <CardContent>
                      <div className="flex flex-wrap gap-2">
                        {Object.entries(stats.by_year)
                          .sort(([a], [b]) => b.localeCompare(a))
                          .map(([year, count]) => (
                            <Badge
                              key={year}
                              variant={
                                yearFilter === year ? "default" : "outline"
                              }
                              className="text-xs cursor-pointer hover:bg-primary/10 transition-colors"
                              onClick={() =>
                                setYearFilter(yearFilter === year ? null : year)
                              }
                            >
                              {year} ({count})
                            </Badge>
                          ))}
                      </div>
                    </CardContent>
                  </Card>
                )}
              </div>
            )}

          {/* Filter bar */}
          <div className="flex items-center gap-3 mb-4 flex-wrap">
            <div className="relative flex-1 min-w-48">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
              <Input
                className="pl-8 h-9 text-sm bg-background"
                value={searchText}
                onChange={(e) => setSearchText(e.target.value)}
                placeholder={t("pages:bibManager.library.searchPlaceholder")}
              />
            </div>
            {allTypes.length > 1 && (
              <Select value={typeFilter} onValueChange={setTypeFilter}>
                <SelectTrigger className="w-36 h-9 bg-background">
                  <Filter className="size-4" />
                  <SelectValue
                    placeholder={t("pages:bibManager.library.filterAllTypes")}
                  />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">
                    {t("pages:bibManager.library.filterAllTypes")}
                  </SelectItem>
                  {allTypes.map((entryType) => (
                    <SelectItem key={entryType} value={entryType}>
                      {entryType}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
            {yearFilter && (
              <Badge
                variant="secondary"
                className="text-xs cursor-pointer hover:bg-destructive/10"
                onClick={() => setYearFilter(null)}
              >
                {yearFilter}
                <span className="ml-1">×</span>
              </Badge>
            )}
            {canWrite && (
              <UndoRedoButtons
                activeFile={selectedBibFile}
                workspaceId={workspaceId}
                onAfterUndoRedo={handleAfterUndoRedo}
              />
            )}
            <Button
              variant="outline"
              size="sm"
              onClick={handleDetectDuplicates}
              disabled={dupChecking || entries.length < 2}
            >
              {dupChecking ? (
                <Loader2 className="animate-spin" />
              ) : (
                <AlertTriangle />
              )}
              {t("pages:bibManager.library.detectDuplicates")}
            </Button>
            {dupTotal > 0 && (
              <Badge variant="destructive" className="text-xs">
                {t("pages:bibManager.library.duplicateFound", {
                  count: dupTotal,
                })}
              </Badge>
            )}
          </div>

          {/* Entry list */}
          {filteredEntries.length === 0 ? (
            <Card>
              <CardContent className="py-12 text-center text-muted-foreground">
                <Library className="size-12 mx-auto mb-3 opacity-30" />
                <p>
                  {entries.length === 0
                    ? t("pages:bibManager.library.noEntries")
                    : t("pages:bibManager.library.noFilterResults")}
                </p>
              </CardContent>
            </Card>
          ) : (
            <div className="space-y-3">
              {filteredEntries.map((entry, index) => {
                const fields = entry.fields || {};
                const title = fields.title || entry.cite_key;
                const author = fields.author || "";
                const year = fields.year || "";
                const doi = fields.doi || "";
                const journal = fields.journal || fields.booktitle || "";

                return (
                  <Card
                    key={`${entry.cite_key}-${index}`}
                    className="hover:border-primary/30 transition-colors [content-visibility:auto] [contain-intrinsic-size:auto_88px]"
                  >
                    <CardContent className="p-4">
                      <div className="flex items-start justify-between gap-3">
                        <div className="flex-1 min-w-0 space-y-1.5">
                          <div className="flex items-center gap-2 flex-wrap">
                            <span className="text-sm font-mono font-semibold text-primary">
                              {entry.cite_key}
                            </span>
                            <Badge variant="secondary" className="text-xs">
                              {entry.entry_type}
                            </Badge>
                            {year && (
                              <span className="text-xs text-muted-foreground">
                                {year}
                              </span>
                            )}
                          </div>
                          <p className="text-sm font-medium truncate">
                            {title}
                          </p>
                          {author && (
                            <p className="text-xs text-muted-foreground truncate">
                              {author}
                            </p>
                          )}
                          {(journal || doi) && (
                            <div className="flex items-center gap-3 text-xs text-muted-foreground">
                              {journal && (
                                <span className="truncate max-w-60">
                                  {journal}
                                </span>
                              )}
                              {doi && (
                                <span className="font-mono truncate max-w-60">
                                  DOI: {doi}
                                </span>
                              )}
                            </div>
                          )}
                        </div>
                        <div className="flex items-center gap-1 shrink-0">
                          {canWrite && (
                            <>
                              <Button
                                variant="ghost"
                                size="icon"
                                className="size-8"
                                onClick={() => handleStartEdit(entry)}
                              >
                                <Edit3 className="size-3.5" />
                              </Button>
                              <Button
                                variant="ghost"
                                size="icon"
                                className="size-8 text-destructive hover:text-destructive"
                                onClick={() => setDeleteTarget(entry)}
                              >
                                <Trash2 className="size-3.5" />
                              </Button>
                            </>
                          )}
                        </div>
                      </div>
                    </CardContent>
                  </Card>
                );
              })}
            </div>
          )}

          <p className="text-xs text-muted-foreground mt-4">
            {t("pages:bibManager.library.showing", {
              filtered: filteredEntries.length,
              total: entries.length,
            })}
          </p>
        </>
      )}

      {/* Edit dialog */}
      <Dialog
        open={!!editEntry}
        onOpenChange={(open) => {
          if (!open) {
            setEditEntry(null);
            setEditBibtex("");
          }
        }}
      >
        <DialogContent className="sm:max-w-xl">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Edit3 className="size-4" />
              {t("pages:bibManager.library.editDialogTitle")}
              {editEntry && (
                <Badge variant="outline" className="text-xs ml-1">
                  {editEntry.cite_key}
                </Badge>
              )}
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-3 py-2">
            <Label className="text-xs">
              {t("pages:bibManager.library.editBibtexLabel")}
            </Label>
            <Textarea
              className="min-h-48 resize-none font-mono text-xs"
              value={editBibtex}
              onChange={(e) => setEditBibtex(e.target.value)}
            />
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setEditEntry(null);
                setEditBibtex("");
              }}
            >
              {t("common:cancel")}
            </Button>
            <Button onClick={handleSaveEdit} disabled={savingEdit}>
              {savingEdit ? (
                <Loader2 className="animate-spin" />
              ) : (
                <CheckCircle />
              )}
              {t("common:save")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete confirmation */}
      <ConfirmDialog
        open={!!deleteTarget}
        onOpenChange={(open) => {
          if (!open) setDeleteTarget(null);
        }}
        title={t("pages:bibManager.library.deleteConfirmTitle")}
        description={
          deleteTarget
            ? t("pages:bibManager.library.deleteConfirmDesc", {
                citeKey: deleteTarget.cite_key,
              })
            : ""
        }
        confirmText={t("common:delete")}
        variant="destructive"
        confirmLoading={deleting}
        onConfirm={handleDeleteConfirm}
      />

      {/* Duplicate results dialog */}
      <Dialog open={showDupDialog} onOpenChange={setShowDupDialog}>
        <DialogContent className="sm:max-w-lg max-h-[80vh] flex flex-col overflow-hidden">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <AlertTriangle className="size-4 text-destructive" />
              {duplicates.length === 0
                ? t("pages:bibManager.library.noDuplicates")
                : t("pages:bibManager.library.duplicateFound", {
                    count: duplicates.length,
                  })}
            </DialogTitle>
          </DialogHeader>
          <div className="flex-1 min-h-0 overflow-y-auto space-y-3 py-2 pr-1 -mr-1">
            {duplicates.length === 0 ? (
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <CheckCircle className="size-4 text-green-500" />
                {t("pages:bibManager.library.allClear")}
              </div>
            ) : (
              duplicates.map((group, idx) => {
                const reasonLabel = (() => {
                  if (group.reason === "doi")
                    return t("pages:bibManager.library.duplicateReasonDoi");
                  if (group.reason === "title")
                    return t("pages:bibManager.library.duplicateReasonTitle");
                  if (group.reason === "cite_key")
                    return t("pages:bibManager.library.duplicateReasonCiteKey");
                  return group.reason;
                })();

                return (
                  <Card
                    key={idx}
                    className="border-destructive/30 [content-visibility:auto] [contain-intrinsic-size:auto_96px]"
                  >
                    <CardContent className="p-3">
                      <div className="flex items-center gap-2 mb-2">
                        <Badge
                          variant={
                            group.confidence === "high"
                              ? "destructive"
                              : "default"
                          }
                          className="text-xs"
                        >
                          {reasonLabel}
                        </Badge>
                        {group.confidence === "high" && (
                          <span className="text-xs text-muted-foreground">
                            {t("pages:bibManager.library.highConfidence")}
                          </span>
                        )}
                      </div>
                      <div className="space-y-1.5">
                        {(group.entries || []).map((e, i) => (
                          <div
                            key={i}
                            className="flex items-center gap-2 text-sm group/dup"
                          >
                            <span className="font-mono text-primary text-xs">
                              {e.cite_key}
                            </span>
                            {e.title && (
                              <span className="text-muted-foreground truncate text-xs flex-1 min-w-0">
                                — {e.title}
                              </span>
                            )}
                            {e.doi && (
                              <Badge
                                variant="outline"
                                className="text-xs font-mono shrink-0"
                              >
                                DOI
                              </Badge>
                            )}
                            {canWrite && (
                              <Button
                                variant="ghost"
                                size="icon"
                                className="size-6 shrink-0 text-muted-foreground hover:text-destructive opacity-0 group-hover/dup:opacity-100 transition-opacity"
                                onClick={() =>
                                  handleDeleteFromDuplicates(e.cite_key)
                                }
                                disabled={deletingDupKey === e.cite_key}
                              >
                                {deletingDupKey === e.cite_key ? (
                                  <Loader2 className="size-3 animate-spin" />
                                ) : (
                                  <Trash2 className="size-3" />
                                )}
                              </Button>
                            )}
                          </div>
                        ))}
                      </div>
                    </CardContent>
                  </Card>
                );
              })
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowDupDialog(false)}>
              {t("common:close")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
