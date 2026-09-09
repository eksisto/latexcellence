import FileMinus from "lucide-react/dist/esm/icons/file-minus.mjs";
import FilePlus from "lucide-react/dist/esm/icons/file-plus.mjs";
import FilePen from "lucide-react/dist/esm/icons/file-pen.mjs";

/** 变更类型的图标 / i18n key / 底色（版本管理页与 diff 抽屉共用） */
export const CHANGE_KIND_META = {
  added: {
    icon: FilePlus,
    key: "changeAdded",
    chip: "bg-primary/10 text-primary",
  },
  modified: {
    icon: FilePen,
    key: "changeModified",
    chip: "bg-accent text-accent-foreground",
  },
  deleted: {
    icon: FileMinus,
    key: "changeDeleted",
    chip: "bg-destructive/10 text-destructive",
  },
};

export const CHANGE_KIND_ORDER = ["added", "modified", "deleted"];
