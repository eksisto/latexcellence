/**
 * 文件树右键菜单映射纯模块（editor-file-context-menu-design §3.1 / 决策 Q13）。
 *
 * 「目标类型 → 菜单项清单」不依赖 React / i18n / DOM，供 node --test 直接
 * 单测菜单矩阵；组件层只负责按清单渲染并把 id 分发给既有 handlers。
 */

/**
 * 解析树节点的菜单目标类型（editor-file-context-menu-design §3.1 矩阵行）。
 * 主文件必为 .tex（后端 set-main-file 白名单约束），isMainFile 隐含 .tex。
 *
 * @param {{ is_dir: boolean, name: string, path: string }} node 树节点
 * @param {string|null} mainFile 当前主文件相对路径（null = 未设置）
 * @returns {{ targetType: "dir"|"tex_file"|"other_file", isMainFile: boolean }}
 */
export function resolveNodeMenuTarget(node, mainFile) {
  if (!node || node.is_dir) {
    return { targetType: "dir", isMainFile: false };
  }
  const isMainFile = mainFile != null && node.path === mainFile;
  const isTex =
    typeof node.name === "string" && node.name.toLowerCase().endsWith(".tex");
  return {
    targetType: isTex || isMainFile ? "tex_file" : "other_file",
    isMainFile,
  };
}

/**
 * 目标类型 → 菜单项清单（展示顺序即数组顺序；group 递增，渲染层在
 * group 变化处插分隔线）。viewer（canWrite=false）不挂菜单，返回空清单。
 *
 * @param {{
 *   targetType: "dir"|"tex_file"|"other_file"|"root",
 *   isMainFile?: boolean,
 *   canWrite?: boolean,
 * }} target 菜单目标（root = 树背景空白区 / 头部标题栏，即项目根）
 * @returns {{ id: string, i18nKey: string, group: number, uploadingSensitive?: boolean }[]}
 *   i18nKey 挂在 components:projectExplorer 命名空间下；uploadingSensitive
 *   的菜单项在上传进行中需禁用（§3.2-5）。
 */
export function getFileTreeMenuItems({
  targetType,
  isMainFile = false,
  canWrite = true,
}) {
  if (!canWrite) return [];
  switch (targetType) {
    case "root":
      return [
        { id: "newFile", i18nKey: "newFile", group: 1 },
        { id: "newFolder", i18nKey: "createFolder", group: 1 },
        {
          id: "upload",
          i18nKey: "upload",
          group: 1,
          uploadingSensitive: true,
        },
      ];
    case "dir":
      return [
        { id: "newFile", i18nKey: "newFileHere", group: 1 },
        { id: "newFolder", i18nKey: "createFolderHere", group: 1 },
        {
          id: "uploadHere",
          i18nKey: "uploadHere",
          group: 1,
          uploadingSensitive: true,
        },
        { id: "rename", i18nKey: "rename", group: 2 },
        { id: "delete", i18nKey: "deleteFolder", group: 2 },
      ];
    case "tex_file":
      if (!isMainFile) {
        return [
          { id: "setMainFile", i18nKey: "setMainFile", group: 1 },
          { id: "rename", i18nKey: "rename", group: 2 },
          { id: "delete", i18nKey: "deleteFile", group: 2 },
        ];
      }
      // 当前主文件不出现「设为主文件」（决策 Q3）
      return [
        { id: "rename", i18nKey: "rename", group: 1 },
        { id: "delete", i18nKey: "deleteFile", group: 1 },
      ];
    case "other_file":
      return [
        { id: "rename", i18nKey: "rename", group: 1 },
        { id: "delete", i18nKey: "deleteFile", group: 1 },
      ];
    default:
      return [];
  }
}
