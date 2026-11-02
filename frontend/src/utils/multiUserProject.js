/**
 * 多用户模式项目打开统一入口（P5）。
 *
 * 职责：
 * 1. 调用 /project/open（多用户模式仅接受 project_id，拒绝任意路径）；
 * 2. 相互独立的请求并行发出，消除打开关键路径的串行瀑布：
 *    · 项目列表（仅取当前项目 role —— 权限实时查库可能已变化，打开时刷新）
 *    · 主文档段落（可选，工作台需要；模块页恢复不需要）
 * 3. 统一写入 store（rootPath / fileTree / mainFile / activeFile / projectId / projectName / projectRole）。
 *
 * 角色回退语义：
 * - 项目列表拉取成功但查无此项目 → "viewer"（只读兜底，访问可能已失效）；
 * - 项目列表拉取失败（网络等）→ null（沿用既有 fallback，调用方自行判定可写性）。
 *
 * WorkspacePage 与 useParagraphCheck / useWritingChat 的恢复路径都走本入口，
 * 避免三处各自实现导致角色同步不一致（刷新直达模块页时 canWrite 依赖陈旧 persist 值）。
 */
import { api } from "@/api/index";
import useStore from "@/store/useStore";

/**
 * @param {string} projectId 目标项目 id
 * @param {string} workspaceId 当前工作区 id
 * @param {object} [options]
 * @param {boolean} [options.preserveActiveFile=false] 保留 store 中已选 activeFile（模块页恢复语义），
 *   否则重置为主文档（工作台打开语义）
 * @param {boolean} [options.withParagraphs=false] 是否并行拉取主文档段落（工作台需要）
 * @returns {Promise<{data: object, role: string|null, paragraphs: array}>}
 */
export async function openMultiUserProject(
  projectId,
  workspaceId,
  { preserveActiveFile = false, withParagraphs = false } = {},
) {
  const store = useStore.getState();
  const data = await api.project.openById(projectId, workspaceId);

  const tasks = [api.projects.list()];
  if (withParagraphs) {
    tasks.push(api.project.paragraphs(data.main_file, workspaceId));
  }
  const [listResult, paragraphsResult] = await Promise.allSettled(tasks);

  // 权限实时查库可能变化，打开后从项目列表同步最新角色与项目名。
  // 列表拉取失败时保留已持久化的项目名（避免刷新后页头小字闪没）。
  let role = null;
  let projectName = store.projectName || null;
  if (listResult.status === "fulfilled") {
    const p = (listResult.value.projects || []).find(
      (x) => x.project_id === projectId,
    );
    role = p?.role || "viewer";
    projectName = p?.name || null;
  }

  store.setRootPath(data.root_path);
  store.setProjectPath(null);
  store.setFileTree(data.file_tree);
  store.setMainFile(data.main_file);
  if (!preserveActiveFile || !store.activeFile) {
    store.setActiveFile(data.main_file);
  }
  store.setOpenedProject({ projectId, projectRole: role, projectName });

  const paragraphs =
    withParagraphs && paragraphsResult.status === "fulfilled"
      ? paragraphsResult.value.paragraphs || []
      : [];

  return { data, role, paragraphs };
}
