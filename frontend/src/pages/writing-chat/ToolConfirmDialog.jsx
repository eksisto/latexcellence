import { useTranslation } from "react-i18next";
import PencilLine from "lucide-react/dist/esm/icons/pencil-line.mjs";
import Undo2 from "lucide-react/dist/esm/icons/undo-2.mjs";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";

export default function ToolConfirmDialog({
  pendingTool,
  onConfirm,
  onCancel,
}) {
  const { t } = useTranslation(["pages", "common"]);

  return (
    <Dialog
      open={!!pendingTool}
      onOpenChange={(open) => {
        if (!open) onCancel();
      }}
    >
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {pendingTool?.tool === "edit_paragraph" ? (
              <PencilLine className="size-4 text-amber-500" />
            ) : (
              <Undo2 className="size-4 text-purple-500" />
            )}
            {t("pages:writingChat.toolConfirmTitle")}
          </DialogTitle>
          <DialogDescription>
            {pendingTool?.tool === "edit_paragraph"
              ? t("pages:writingChat.toolConfirmEditDesc", {
                  file: pendingTool?.args?.file_path || "",
                  index: pendingTool?.args?.paragraph_index ?? "",
                })
              : pendingTool?.tool === "undo_last_edit"
                ? t("pages:writingChat.toolConfirmUndoDesc", {
                    file: pendingTool?.args?.file_path || "",
                  })
                : ""}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={onCancel}>
            {t("common:cancel")}
          </Button>
          <Button onClick={() => onConfirm()}>
            {t("pages:writingChat.toolConfirmApply")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
