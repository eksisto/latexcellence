/**
 * 工作区展示名归约（PageHeader 的「当前工作区」小字唯一数据源）。
 *
 * - 多用户模式：项目有独立名称，由项目列表 / 创建接口带回并写入
 *   `store.projectName`（多用户下不暴露 rootPath，不能从路径取名）；
 * - 单用户模式：项目没有独立名称，取打开路径的最后一段。优先使用
 *   projectPath（用户实际选择的目录），缺失时回退 rootPath（后端解析后的
 *   真实目录）。
 *
 * 纯函数无副作用，供 `WorkspaceBadge` 与单测复用。
 */

/**
 * 取路径最后一段，兼容 POSIX（/）与 Windows（\）分隔符。
 *
 * @param {string|null|undefined} path
 * @returns {string}
 */
export function pathBasename(path) {
  if (typeof path !== "string") return "";
  const withoutTrailing = path.replace(/[\\/]+$/, "");
  if (!withoutTrailing) return "";
  const segments = withoutTrailing.split(/[\\/]/);
  return segments[segments.length - 1] || "";
}

/**
 * 解析当前工作区要展示的名称。
 *
 * @param {object} p
 * @param {boolean} p.multiUser
 * @param {string|null} [p.projectName] 多用户项目名
 * @param {string|null} [p.projectPath] 单用户打开的项目路径
 * @param {string|null} [p.rootPath] 后端解析后的项目根路径
 * @returns {string} 无可用名称时为空串（调用方据此不渲染）
 */
export function resolveWorkspaceName({
  multiUser,
  projectName,
  projectPath,
  rootPath,
}) {
  if (multiUser) return (projectName || "").trim();
  return (
    pathBasename(projectPath) ||
    pathBasename(rootPath) ||
    (projectName || "").trim()
  );
}
