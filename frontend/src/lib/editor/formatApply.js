/**
 * 一键格式化（editor-productivity-plan §5.3 阶段五 / 决策 Q8）。
 *
 * 交互语义（Q8）：点击格式化 → 调后端 POST /api/project/format-latex
 * 纯转换端点 → 成功后**直接全文替换发起时的 tab 缓冲区**并置脏（落盘
 * 仍走 save-file 单一路径 / 自动保存）+ toast 提示 Ctrl+Z 可撤销；
 * **不做 diff 预览**。
 *
 * 撤销兜底（CM6 history 语义）：整文档替换必须走**单个事务**——
 *   - 激活 tab：对 CM6 视图 dispatch 单个 changes 事务（历史记录一次
 *     变更，Ctrl+Z 一步撤销回原文）；
 *   - 后台 tab：请求在途期间活跃位可能被切走，此时对 tabViewRegistry
 *     里的 EditorState 存档做单事务 update（history facet 随存档保留，
 *     换回 tab 后 Ctrl+Z 仍一步撤销）；
 *   - 存档缺失 / 失配时降级为仅写回 store 缓冲区（下次换入按缓冲区
 *     重建，历史复位——可接受降级，Q8 兜底靠 toast 提示而非强保证）。
 *
 * 快捷键：Ctrl/Cmd+Shift+F（避开既有键位——阶段二占用 Ctrl+B/I/U/K/L、
 * Ctrl+/；CM6 内置查找面板为 Ctrl/Cmd+F，Shift 变体未被占用，浏览器
 * 亦无保留冲突）。本模块提供 isFormatShortcut 判定纯函数与常量。
 */

/** 格式化快捷键（窗口级监听；Mod = macOS Cmd / 其余 Ctrl） */
export const FORMAT_SHORTCUT_LABEL = "Ctrl+Shift+F";

/**
 * 归一化格式化目标 tab 路径：仅接受非空字符串 filePath，其余（含
 * 工具栏误传的点击事件对象、undefined）回落到激活 tab 路径。
 * @param {unknown} filePath 调用方传入的目标（应为 string，防御非字符串）
 * @param {string|null} fallbackPath 激活 tab 路径
 * @returns {string|null}
 */
export function resolveFormatTargetPath(filePath, fallbackPath) {
  if (typeof filePath === "string" && filePath) return filePath;
  return fallbackPath ?? null;
}

/**
 * 判定键盘事件是否为格式化快捷键（Ctrl/Cmd+Shift+F）。
 * @param {{ctrlKey?: boolean, metaKey?: boolean, shiftKey?: boolean, key?: string}} e
 * @returns {boolean}
 */
export function isFormatShortcut(e) {
  if (!e) return false;
  if (!(e.ctrlKey || e.metaKey)) return false;
  if (!e.shiftKey) return false;
  return e.key === "f" || e.key === "F";
}

/**
 * 构造整文档替换的单事务 changes 规格（CM6 TransactionSpec.changes）。
 * @param {number} docLength 当前文档长度
 * @param {string} formatted 格式化后的全文
 */
export function buildFormatChanges(docLength, formatted) {
  return { from: 0, to: docLength, insert: formatted };
}

/**
 * 格式化结果应用决策（阶段五 / Q8）：返回后按发起 tab 与当前视图状态
 * 选择替换通路——目标始终是**发起时的 tab**（按 filePath 定位），活跃
 * 已切走时不切视图，只替换发起 tab 的缓冲区数据。
 *
 * @param {object} input
 * @param {string|null} input.targetPath  发起格式化时的 tab 路径
 * @param {string|null} input.activePath  响应返回时刻的激活 tab 路径
 * @param {string|null} input.viewDoc     CM6 视图当前装载的文档全文
 *   （null = 视图不可用）
 * @param {string|null} input.archivedDoc 发起 tab 的 EditorState 存档文档
 *   全文（null = 无存档）
 * @param {string|null} input.targetBuffer 发起 tab 当前的 store 缓冲区
 *   （null = tab 已被关闭）
 * @param {string} input.formatted 格式化结果全文
 * @returns {"view"|"state"|"store"|"skip_missing"|"skip_unchanged"}
 *   - view：目标即激活 tab 且视图正显示其缓冲区 → 对视图单事务 dispatch；
 *   - state：存档文档与缓冲区一致 → 对存档单事务 update（历史保留）；
 *   - store：存档缺失 / 失配 → 仅写回 store（换入时重建，历史复位）；
 *   - skip_missing：发起 tab 已被关闭；
 *   - skip_unchanged：结果与原文一致（无需替换，提示即可）。
 */
export function planFormatApply({
  targetPath,
  activePath,
  viewDoc,
  archivedDoc,
  targetBuffer,
  formatted,
}) {
  if (typeof targetBuffer !== "string") return "skip_missing";
  if (formatted === targetBuffer) return "skip_unchanged";
  // 目标即激活 tab 且视图确在显示该缓冲区（三条件防误写：仅比对文档
  // 内容会在「另一 tab 恰好装载相同内容」时把替换写到错误 tab 的视图，
  // 必须锚定发起路径；双内容条件防视图换档瞬间的误写）：
  // 视图历史才是激活 tab 的真身，必须走 dispatch 保住 Ctrl+Z 语义
  if (
    activePath !== null &&
    activePath === targetPath &&
    viewDoc !== null &&
    viewDoc === targetBuffer
  ) {
    return "view";
  }
  if (archivedDoc !== null && archivedDoc === targetBuffer) return "state";
  return "store";
}

/**
 * 对 CM6 视图执行整文档替换（单事务，Ctrl+Z 一步撤销）。
 * @param {import("@codemirror/view").EditorView|null} view
 * @param {string} formatted
 * @returns {boolean} 是否执行了替换（视图不可用 / 只读 / 内容一致时不执行）
 */
export function applyFormatToView(view, formatted) {
  if (!view || view.destroyed || view.state.readOnly) return false;
  const docText = view.state.doc.toString();
  if (docText === formatted) return false;
  view.dispatch({
    changes: buildFormatChanges(docText.length, formatted),
    scrollIntoView: true,
  });
  view.focus();
  return true;
}

/**
 * 对 EditorState 存档执行整文档替换（单事务 update，history facet 随
 * 派生状态保留——换回 tab 后 Ctrl+Z 仍一步撤销）。
 * 注意 state.update() 返回 Transaction，其 .state 才是应用事务后的新
 * 状态（经全部 state field 处理，history 已记录本次变更）。
 * @param {import("@codemirror/state").EditorState|null} state
 * @param {string} formatted
 * @returns {import("@codemirror/state").EditorState|null} 新状态；状态不可用
 *   或内容一致时返回 null（调用方降级走 store 通路）
 */
export function applyFormatToState(state, formatted) {
  if (!state || state.doc.toString() === formatted) return null;
  return state.update({
    changes: buildFormatChanges(state.doc.length, formatted),
  }).state;
}
