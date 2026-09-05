/**
 * 跨文件大纲分组与置顶排序纯模块（editor-experience-design §4.4 / 决策
 * Q10，阶段四）。
 *
 * 数据源为后端 GET /api/project/outline 的全项目 .tex 分节扫描（零 LLM、
 * 只读）：`files` 为 [{file_path, entries: [{level, title, line}]}]，
 * file_path 一律为项目内相对路径。本模块把扫描结果整理为左栏大纲面板
 * 「本项目」视图的分组展示结构：
 *   - 按文件分组（首轮平铺，\input/\include 树状排序明确不做）；
 *   - 激活文件所在组置顶（active: true），其余组按 file_path 字典序
 *     （与后端 sorted(rglob) 的序一致，前后端口径稳定）；
 *   - 空条目组剔除（后端已保证非空，前端兜底再滤一次，防脏数据渲染空组）。
 *
 * 纯函数，不 import API 层与 store，供 node --test 直接单测。
 */

/**
 * 后端 outline 响应 → 分组展示结构。
 * @param {Array<{file_path: string, entries: Array}>|null} files 后端扫描结果
 * @param {string|null} activePath 当前激活 tab 的文件相对路径
 * @returns {Array<{file_path: string, entries: Array, active: boolean}>}
 */
export function groupProjectOutline(files, activePath) {
  const list = Array.isArray(files) ? files : [];
  const groups = [];
  for (const file of list) {
    const entries = Array.isArray(file?.entries) ? file.entries : [];
    if (entries.length === 0) continue;
    groups.push({
      file_path: file.file_path,
      entries,
      active: file.file_path === activePath,
    });
  }
  groups.sort((a, b) => {
    if (a.active !== b.active) return a.active ? -1 : 1;
    if (a.file_path < b.file_path) return -1;
    if (a.file_path > b.file_path) return 1;
    return 0;
  });
  return groups;
}
