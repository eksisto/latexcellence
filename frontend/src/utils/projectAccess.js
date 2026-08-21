/**
 * 多用户/单用户双模式的项目打开与写权限判定（单一事实源）。
 *
 * 职责：
 * 1. `isProjectOpened`：模块页空态守卫的"项目已打开"判定——
 *    单用户按 projectPath，多用户按 projectId（多用户下 projectPath 恒为 null，
 *    直接判 projectPath 会导致功能整体不可达）；
 * 2. `hasWriteRole`：写角色白名单（owner / editor）。采用白名单而非
 *    "非 viewer"反白名单：角色缺失（如列表拉取失败得到 null）时按只读兜底，
 *    宁可 UI 少给写入口（后端 403 兜底仍在），不可静默放行。
 *
 * 纯函数无副作用，供 useIsOpened / useCanWrite 及单测复用。
 */

/**
 * 项目是否已打开（按当前模式选择判定键）。
 *
 * @param {object} p
 * @param {boolean} p.multiUser 是否多用户模式
 * @param {string|null} p.projectPath 单用户模式的本地路径
 * @param {string|null} p.projectId 多用户模式的项目 id
 * @returns {boolean}
 */
export function isProjectOpened({ multiUser, projectPath, projectId }) {
  return multiUser ? Boolean(projectId) : Boolean(projectPath);
}

/**
 * 角色是否可写（白名单：owner / editor；未知角色一律只读）。
 *
 * @param {string|null|undefined} projectRole 项目角色
 * @returns {boolean}
 */
export function hasWriteRole(projectRole) {
  return projectRole === "owner" || projectRole === "editor";
}

/**
 * 空态 CTA 的去向与文案 key（模式感知）：
 * 单用户回工作区（/workspace），多用户回项目工作台（/projects）。
 *
 * @param {boolean} multiUser 是否多用户模式
 * @returns {{to: string, labelKey: string}} labelKey 为 common 命名空间下的 key
 */
export function emptyStateCta(multiUser) {
  return multiUser
    ? { to: "/projects", labelKey: "openProjects" }
    : { to: "/workspace", labelKey: "openWorkspace" };
}
