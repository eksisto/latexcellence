/**
 * 多缓冲区 tab 状态机纯模块（editor-productivity-design §5.1，阶段一）。
 *
 * editorSession 切片的不可变归约全部收敛在此：store 只做「调用归约 + 写回」，
 * 不内联 tab 数组操作，保证打开 / 激活 / 关闭 / 保存回写等转移逻辑可被
 * node --test 直接单测。本模块不感知 React / Zustand / CM6 / 网络。
 *
 * 会话状态形状（session）：
 *   {
 *     tabs: [{ filePath, buffer, savedBuffer, openedBuffer, baseMd5,
 *              dirty, stale, lastActive }],
 *     activeTabPath: string | null,
 *   }
 *   - filePath：项目相对路径，tab 唯一键；
 *   - buffer：缓冲区全文（受控 value 的单一事实源）；
 *   - savedBuffer：最近一次保存落盘的内容基线（保存快照回写，阶段八）；
 *   - openedBuffer：本次会话打开时的原始内容基线（阶段八，仅内存态——
 *     不进会话持久化，刷新恢复后回落为 savedBuffer）；
 *   - baseMd5：打开（或上次保存）时的磁盘 MD5，乐观锁基线；
 *   - dirty：buffer ≠ savedBuffer（内容比对，Ctrl+Z 回到保存点即清脏）；
 *   - stale：磁盘已变更标记（激活时惰性 file-md5 比对不一致）；
 *   - lastActive：最近激活时刻（LRU 淘汰依据），由调用方传入时刻值。
 *
 * cursor/selection 随 CM6 EditorState 天然隔离、滚动位置由 tabViews 登记表
 * 持有，均不进入本状态（editor-productivity-design §4）。
 */

/**
 * 构造单个 tab 记录。
 * @param {{filePath: string, buffer: string, baseMd5: string|null, now?: number}} input
 * @returns {{filePath: string, buffer: string, savedBuffer: string, openedBuffer: string, baseMd5: string|null, dirty: boolean, stale: boolean, lastActive: number}}
 */
export function createTabRecord({ filePath, buffer, baseMd5, now = 0 }) {
  return {
    filePath,
    buffer,
    // 新开 tab 即保存基线与打开基线的起点（阶段八）
    savedBuffer: buffer,
    openedBuffer: buffer,
    baseMd5,
    dirty: false,
    stale: false,
    lastActive: now,
  };
}

/* ── 只读选择器（入参为 session 或 {tabs, activeTabPath} 形状） ────────── */

/**
 * @param {{tabs: Array}} session
 * @param {string} filePath
 * @returns {object|undefined}
 */
export function findTab(session, filePath) {
  return session.tabs.find((tab) => tab.filePath === filePath);
}

/** @returns {object|null} 激活 tab（不存在时 null） */
export function getActiveTab(session) {
  if (!session.activeTabPath) return null;
  return findTab(session, session.activeTabPath) ?? null;
}

/** @returns {string|null} 激活 tab 路径（等价 session.activeTabPath，防御悬空） */
export function activeFilePath(session) {
  return getActiveTab(session)?.filePath ?? null;
}

/** @returns {string} 激活 tab 缓冲区全文（无激活 tab 时空串） */
export function activeBuffer(session) {
  return getActiveTab(session)?.buffer ?? "";
}

/** @returns {string|null} 激活 tab 乐观锁基线 */
export function activeBaseMd5(session) {
  return getActiveTab(session)?.baseMd5 ?? null;
}

/** @returns {boolean} 激活 tab 是否有未保存修改 */
export function activeDirty(session) {
  return getActiveTab(session)?.dirty ?? false;
}

/**
 * 激活 tab 的保存内容基线（阶段八：修改行色条橙色基线；无激活 tab 或
 * 基线缺失时 null——编辑器侧按「无基线」降级为不显示色条）。
 * @returns {string|null}
 */
export function activeSavedBuffer(session) {
  return getActiveTab(session)?.savedBuffer ?? null;
}

/**
 * 激活 tab 的打开内容基线（阶段八：绿色基线，仅会话内存态）。
 * @returns {string|null}
 */
export function activeOpenedBuffer(session) {
  return getActiveTab(session)?.openedBuffer ?? null;
}

/** @returns {string} 指定 tab 的缓冲区全文（未找到时空串） */
export function getTabBuffer(session, filePath) {
  return findTab(session, filePath)?.buffer ?? "";
}

/** @returns {number} 脏 tab 数量 */
export function countDirtyTabs(tabs) {
  return tabs.reduce((n, tab) => n + (tab.dirty ? 1 : 0), 0);
}

/** @returns {Array} 脏 tab 列表（保持 tab 条顺序，编译按此顺序落盘） */
export function getDirtyTabsInOrder(tabs) {
  return tabs.filter((tab) => tab.dirty);
}

/* ── 不可变归约（入参 session，返回新的 {tabs, activeTabPath} 片段） ────── */

/**
 * 打开 tab：已存在则激活（不改缓冲区——磁盘刷新走显式 reload 语义），
 * 不存在则新建记录并激活。
 * @param {{tabs: Array, activeTabPath: string|null}} session
 * @param {{filePath: string, buffer: string, baseMd5: string|null, now?: number}} input
 * @returns {{tabs: Array, activeTabPath: string}}
 */
export function openTabReducer(
  session,
  { filePath, buffer, baseMd5, now = 0 },
) {
  const existing = findTab(session, filePath);
  if (existing) {
    // 打开已有 tab：仅激活并刷新基线（调用方仅在首开 / 显式重拉时携带
    // 服务端内容，正常切换文件树点击走 activateTabReducer）。
    // 重拉即「回到磁盘真实内容」：保存基线与打开基线一并复位（阶段八），
    // 修改行色条随之清零
    const tabs = session.tabs.map((tab) =>
      tab.filePath === filePath
        ? {
            ...tab,
            buffer,
            savedBuffer: buffer,
            openedBuffer: buffer,
            baseMd5,
            dirty: false,
            stale: false,
            lastActive: now,
          }
        : tab,
    );
    return { tabs, activeTabPath: filePath };
  }
  return {
    tabs: [
      ...session.tabs,
      createTabRecord({ filePath, buffer, baseMd5, now }),
    ],
    activeTabPath: filePath,
  };
}

/**
 * 激活 tab：刷新 lastActive（LRU 依据）。tab 不存在时原样返回原引用。
 * @param {{tabs: Array, activeTabPath: string|null}} session
 * @param {string} filePath
 * @param {number} now
 * @returns {{tabs: Array, activeTabPath: string|null}}
 */
export function activateTabReducer(session, filePath, now) {
  const tab = findTab(session, filePath);
  if (!tab) return session;
  if (session.activeTabPath === filePath && tab.lastActive === now) {
    return session;
  }
  return {
    tabs: session.tabs.map((t) =>
      t.filePath === filePath ? { ...t, lastActive: now } : t,
    ),
    activeTabPath: filePath,
  };
}

/**
 * 键入回写：更新激活 tab 的缓冲区并按保存基线重算脏标记、刷新 lastActive。
 * 脏 = buffer ≠ savedBuffer（阶段八）：Ctrl+Z 撤回到保存点内容即自动清脏，
 * 与修改行色条的语义一致。无激活 tab 时原样返回原引用（与 v1
 * setBufferContent 的空转守卫一致）。
 * @param {{tabs: Array, activeTabPath: string|null}} session
 * @param {string} buffer CM6 回传的全文
 * @param {number} now
 * @returns {{tabs: Array, activeTabPath: string|null}}
 */
export function setTabBufferReducer(session, buffer, now) {
  const active = getActiveTab(session);
  if (!active) return session;
  return {
    tabs: session.tabs.map((t) =>
      t.filePath === active.filePath
        ? {
            ...t,
            buffer,
            dirty: buffer !== t.savedBuffer,
            lastActive: now,
          }
        : t,
    ),
    activeTabPath: session.activeTabPath,
  };
}

/**
 * 后台 tab 缓冲区替换（一键格式化的非激活通路，阶段五 / Q8）：
 * 更新指定 tab 的缓冲区并置脏（结果只进缓冲区，落盘仍走 save-file
 * 单一路径）；不改激活位 / 乐观锁基线 / 陈旧标记——格式化不触磁盘。
 * tab 不存在时原样返回原引用。
 * @param {{tabs: Array, activeTabPath: string|null}} session
 * @param {string} filePath 目标 tab（格式化发起时的 tab）
 * @param {string} buffer 格式化后的全文
 * @returns {{tabs: Array, activeTabPath: string|null}}
 */
export function replaceTabBufferReducer(session, filePath, buffer) {
  const tab = findTab(session, filePath);
  if (!tab || tab.buffer === buffer) return session;
  return {
    tabs: session.tabs.map((t) =>
      t.filePath === filePath
        ? { ...t, buffer, dirty: buffer !== t.savedBuffer }
        : t,
    ),
    activeTabPath: session.activeTabPath,
  };
}

/**
 * 保存成功回写：更新乐观锁基线与保存内容基线（色条的橙色 → 绿色分界，
 * 阶段八）、清除陈旧标记，并按保存快照重算脏标记（在途期间有新输入 →
 * 保持 dirty，避免"已保存"假象导致静默丢失）。
 * @param {{tabs: Array, activeTabPath: string|null}} session
 * @param {{filePath: string, md5: string, savedBuffer?: string}} input
 * @returns {{tabs: Array, activeTabPath: string|null}}
 */
export function markTabSavedReducer(session, { filePath, md5, savedBuffer }) {
  return {
    tabs: session.tabs.map((t) =>
      t.filePath === filePath
        ? {
            ...t,
            baseMd5: md5,
            // 保存快照缺失时以当前缓冲区为基线（防御旧调用方）
            savedBuffer:
              typeof savedBuffer === "string" ? savedBuffer : t.buffer,
            stale: false,
            dirty:
              typeof savedBuffer === "string"
                ? t.buffer !== savedBuffer
                : false,
          }
        : t,
    ),
    activeTabPath: session.activeTabPath,
  };
}

/**
 * 陈旧标记回写（激活时 file-md5 比对结果）。
 * @param {{tabs: Array, activeTabPath: string|null}} session
 * @param {string} filePath
 * @param {boolean} stale
 * @returns {{tabs: Array, activeTabPath: string|null}}
 */
export function setTabStaleReducer(session, filePath, stale) {
  const tab = findTab(session, filePath);
  if (!tab || tab.stale === stale) return session;
  return {
    tabs: session.tabs.map((t) =>
      t.filePath === filePath ? { ...t, stale } : t,
    ),
    activeTabPath: session.activeTabPath,
  };
}

/**
 * 关闭 tab：移除记录；若关闭的是激活 tab，则激活同位置的下一个 tab，
 * 末尾则激活前一个（对齐常见编辑器行为）；清空后 activeTabPath 置 null。
 * @param {{tabs: Array, activeTabPath: string|null}} session
 * @param {string} filePath
 * @returns {{tabs: Array, activeTabPath: string|null}}
 */
export function closeTabReducer(session, filePath) {
  const index = session.tabs.findIndex((tab) => tab.filePath === filePath);
  if (index === -1) return session;
  const tabs = session.tabs.filter((tab) => tab.filePath !== filePath);
  let activeTabPath = session.activeTabPath;
  if (activeTabPath === filePath) {
    activeTabPath = tabs[Math.min(index, tabs.length - 1)]?.filePath ?? null;
  }
  return { tabs, activeTabPath };
}

/**
 * 关闭全部 tab / 会话复位（项目切换守卫）。
 * @returns {{tabs: Array, activeTabPath: null}}
 */
export function resetSessionReducer() {
  return { tabs: [], activeTabPath: null };
}

/* ── 保存 / 关闭 / 自动保存的前置决策（hook 门户逻辑收敛，便于单测） ── */

/**
 * 保存前置决策（useEditorBuffer.doSaveTab 的门户矩阵）。
 * @param {object|null} tab 待保存的 tab 记录
 * @param {{conflictPath?: string|null, overwrite?: boolean, autosave?: boolean,
 *   confirmedStale?: boolean}} [options]
 * @returns {"proceed"|"blocked"|"stale_confirm"}
 *   - proceed：可发起网络保存；
 *   - blocked：前置不满足，静默不保存（含自动保存通道对陈旧 tab 的静默跳过）；
 *   - stale_confirm：磁盘已变更需用户确认（调用方抢激活位并弹确认窗）。
 */
export function planTabSave(
  tab,
  {
    conflictPath = null,
    overwrite = false,
    autosave = false,
    confirmedStale = false,
  } = {},
) {
  if (!tab) return "blocked";
  // 冲突弹窗待决期间不另起新保存（防 409 回路）；覆盖保存是弹窗自己的
  // 出路，必须放行——否则「覆盖保存」按钮会被本守卫永久拦截
  if (!overwrite && conflictPath && conflictPath === tab.filePath) {
    return "blocked";
  }
  if (overwrite) return "proceed";
  if (!tab.dirty) return "blocked";
  // 陈旧守卫（Q4）：磁盘已变更需用户确认后才落盘；自动保存通道静默跳过
  if (tab.stale && !confirmedStale) {
    return autosave ? "blocked" : "stale_confirm";
  }
  return "proceed";
}

/**
 * 关闭入口决策：干净（或已不存在）直接关；脏转三选弹窗（Q5）。
 * 「保存并关闭」复用同一决策——弹窗待决期间 tab 若已被自动保存落盘
 * （变干净）或经其他通路关闭，直接关闭而非对干净 tab 发保存请求。
 * @param {{tabs: Array, activeTabPath: string|null}} session
 * @param {string} filePath
 * @returns {"close"|"confirm"}
 */
export function planTabClose(session, filePath) {
  const tab = findTab(session, filePath);
  return tab && tab.dirty ? "confirm" : "close";
}

/**
 * 对比前后 tabs，产出自动保存防抖器的武装 / 清除清单。
 * 「新增 tab」也计入武装——刷新恢复（Q14）的脏 tab 在 hook 挂载对齐之后
 * 才经 restoreSession 出现，必须在此补武装，否则恢复后不触摸就永不落盘。
 * 武装是否生效（autoSave 偏好 / 脏标 / 冲突门闩）由调用方的 armAutosave
 * 自过滤，这里只负责「状态可能需要（重新）武装」的判定。
 * @param {Array} prevTabs
 * @param {Array} nextTabs
 * @returns {{toArm: string[], toClear: string[]}}
 */
export function diffTabsForAutosave(prevTabs, nextTabs) {
  const prevMap = new Map(prevTabs.map((tab) => [tab.filePath, tab]));
  const nextPaths = new Set(nextTabs.map((tab) => tab.filePath));
  const toArm = [];
  for (const tab of nextTabs) {
    const before = prevMap.get(tab.filePath);
    if (
      !before ||
      before.buffer !== tab.buffer ||
      before.dirty !== tab.dirty ||
      before.stale !== tab.stale
    ) {
      toArm.push(tab.filePath);
    }
  }
  const toClear = prevTabs
    .filter((tab) => !nextPaths.has(tab.filePath))
    .map((tab) => tab.filePath);
  return { toArm, toClear };
}

/* ── 移动后的路径重映射（editor-file-move-design §5.2 / 决策 Q6） ──────── */

/**
 * 单路径重映射核心（拖拽移动成功后的路径跟随）：文件按精确匹配；目录按
 * 「等于 from 或位于 from 子树内」的前缀一次匹配覆盖整棵子树。前缀比较
 * 带 `/` 边界（`figs` 不误伤 `figs2`）。
 * @param {string} path 待重映射的路径
 * @param {string} fromPath 移动前相对路径
 * @param {string} toPath 移动后相对路径
 * @param {boolean} [isDir] 被移动对象是否为目录
 * @returns {string} 命中时返回新路径；未命中（或输入非法）原样返回
 */
export function remapMovedPath(path, fromPath, toPath, isDir = false) {
  if (typeof path !== "string" || !fromPath || !toPath) return path;
  if (isDir) {
    if (path === fromPath) return toPath;
    if (path.startsWith(`${fromPath}/`)) {
      return toPath + path.slice(fromPath.length);
    }
    return path;
  }
  return path === fromPath ? toPath : path;
}

/**
 * 移动成功后的会话路径重映射：重映射 tabs[].filePath 与 activeTabPath；
 * buffer / savedBuffer / openedBuffer / baseMd5 / dirty / stale /
 * lastActive 全部原样保留（Q6：路径重映射是唯一不丢数据的方案）。
 * 未命中任何路径时原样返回 session 引用，供 store 层空转（不触发订阅方）。
 * @param {{tabs: Array, activeTabPath: string|null}} session
 * @param {string} fromPath 移动前相对路径
 * @param {string} toPath 移动后相对路径
 * @param {{isDir?: boolean}} [options]
 * @returns {{tabs: Array, activeTabPath: string|null}}
 */
export function remapMovedPaths(
  session,
  fromPath,
  toPath,
  { isDir = false } = {},
) {
  let changed = false;
  const tabs = session.tabs.map((tab) => {
    const next = remapMovedPath(tab.filePath, fromPath, toPath, isDir);
    if (next === tab.filePath) return tab;
    changed = true;
    return { ...tab, filePath: next };
  });
  const activeTabPath = remapMovedPath(
    session.activeTabPath,
    fromPath,
    toPath,
    isDir,
  );
  if (activeTabPath !== session.activeTabPath) changed = true;
  if (!changed) return session;
  return { tabs, activeTabPath };
}
