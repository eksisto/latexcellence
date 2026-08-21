/**
 * 多用户模式写权限（单一事实源）：viewer 只读，其余角色可写；单用户模式始终可写。
 *
 * 各模块页 / hooks 统一引用本 hook，避免 `!isMultiUser() || projectRole !== "viewer"`
 * 在多处复制导致权限模型演化时漏改（后端 403 仍兜底）。
 */
import useStore from "@/store/useStore";
import { isMultiUser } from "@/api/connection";

export default function useCanWrite() {
  const projectRole = useStore((s) => s.projectRole);
  return !isMultiUser() || projectRole !== "viewer";
}
