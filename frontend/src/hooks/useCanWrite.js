/**
 * 多用户模式写权限（单一事实源）：白名单 owner / editor 可写，
 * viewer 及角色未知（如项目列表拉取失败得到 null）一律只读；单用户模式始终可写。
 *
 * 白名单而非"非 viewer"反白名单：宁可 UI 少给写入口（后端 403 仍兜底），
 * 不可在角色缺失时静默放行。各模块页 / hooks 统一引用本 hook。
 */
import useStore from "@/store/useStore";
import { isMultiUser } from "@/api/connection";
import { hasWriteRole } from "@/utils/projectAccess";

export default function useCanWrite() {
  const projectRole = useStore((s) => s.projectRole);
  return !isMultiUser() || hasWriteRole(projectRole);
}
