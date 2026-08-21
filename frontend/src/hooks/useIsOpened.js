/**
 * 项目是否已打开（模式感知，见 utils/projectAccess.js）。
 * 模块页空态守卫统一引用本 hook，避免 7 处同形 `!projectPath` 条件漏改。
 */
import useStore from "@/store/useStore";
import { isMultiUser } from "@/api/connection";
import { isProjectOpened } from "@/utils/projectAccess";

export default function useIsOpened() {
  const projectPath = useStore((s) => s.projectPath);
  const projectId = useStore((s) => s.projectId);
  return isProjectOpened({
    multiUser: isMultiUser(),
    projectPath,
    projectId,
  });
}
