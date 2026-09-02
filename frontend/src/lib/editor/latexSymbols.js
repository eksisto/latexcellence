/**
 * 引用符号缓存与补全候选项映射（editor-productivity-plan §5.2/§5.3，
 * 决策 Q7，阶段四）。
 *
 * 数据源为后端 GET /api/project/latex-symbols 的全项目只读符号扫描
 * （零 LLM）：`labels` 为 [{key, file_path, line}]，`cites` 为
 * [{key, file_path, kind, title?}]（kind = "bib" 时带条目标题）。
 *
 * 本模块只承载纯逻辑，不直接 import API 层（node --test 可直接导入）：
 *   - 缓存以项目为键（单用户 projectPath / 多用户 projectId，见
 *     reduceProjectKey），保存成功后由 useEditorBuffer 调 invalidate
 *     失效，项目切换/关闭时 clear；拉取以共享在途 Promise 去重，失败
 *     静默出缓存（下次触发补全重拉，不弹 toast）；
 *   - buildLabelOptions / buildCiteOptions 把扫描结果映射为 CM6 补全
 *     option（label 显示名 + detail 显示出处，type 区分 label/cite 供
 *     弹层样式挂钩），filterCompletionOptions 按已敲入的前缀过滤排序。
 *
 * 弹层外观复用 completionTheme.js 的学术索引卡主题。
 */

/** 单次弹出的候选上限（大项目全量列表过长，截断保体验） */
const MAX_OPTIONS = 60;

/**
 * 项目隔离键归约：单用户看 projectPath，多用户看 projectId
 * （与 useEditorBuffer 的 projectKey 口径一致）。
 * @param {{projectPath?: string|null, projectId?: string|null}} [project]
 * @returns {string|null}
 */
export function reduceProjectKey({ projectPath, projectId } = {}) {
  return projectPath || projectId || null;
}

/**
 * 创建项目符号缓存（load/invalidate/clear；应用级单例见
 * projectSymbolCache）。fetcher 由调用方注入（通常为
 * api.project.latexSymbols(workspaceId)），保持本模块零 API 依赖。
 * @returns {{load: Function, invalidate: Function, clear: Function,
 *   size: number}}
 */
export function createProjectSymbolCache() {
  /** @type {Map<string, Promise<object|null>>} projectKey -> 在途/就绪数据 */
  const inflight = new Map();
  return {
    /**
     * 取项目符号数据：缓存缺失时经 fetcher 拉取（在途 Promise 共享），
     * 拉取失败静默出缓存并返回 null，下次触发补全重试。
     * @param {string|null} projectKey 项目隔离键
     * @param {() => Promise<object>} fetcher 拉取函数
     * @returns {Promise<object|null>}
     */
    load(projectKey, fetcher) {
      if (!projectKey || typeof fetcher !== "function") {
        return Promise.resolve(null);
      }
      const cached = inflight.get(projectKey);
      if (cached) return cached;
      const promise = Promise.resolve()
        .then(fetcher)
        .then(
          (data) => data ?? null,
          (e) => {
            inflight.delete(projectKey);
            return null;
          },
        );
      inflight.set(projectKey, promise);
      return promise;
    },
    /** 保存成功后失效：下次触发补全重拉（Q7 缓存失效策略） */
    invalidate(projectKey) {
      if (projectKey) inflight.delete(projectKey);
    },
    /** 项目切换 / 关闭时清空全部缓存 */
    clear() {
      inflight.clear();
    },
    get size() {
      return inflight.size;
    },
  };
}

/** 应用级单例：SessionEditor（拉取）与 useEditorBuffer（失效）共享 */
export const projectSymbolCache = createProjectSymbolCache();

/* ── 补全候选项映射 ──────────────────────────────────────────────────── */

/**
 * label 列表 → 补全 option：label 为标签名，detail 为 `文件:行号` 出处。
 * @param {object|null} data latex-symbols 扫描结果
 * @returns {Array<{label: string, detail: string, type: string, boost: number}>}
 */
export function buildLabelOptions(data) {
  const labels = Array.isArray(data?.labels) ? data.labels : [];
  return labels.map((item) => ({
    label: item.key,
    detail: `${item.file_path}:${item.line}`,
    type: "label",
    boost: 0,
  }));
}

/**
 * cite 列表 → 补全 option：bib 条目 detail 显示标题（缺省回落文件），
 * .tex 已引用 key 显示文件出处。
 * @param {object|null} data latex-symbols 扫描结果
 * @returns {Array<{label: string, detail: string, type: string, boost: number}>}
 */
export function buildCiteOptions(data) {
  const cites = Array.isArray(data?.cites) ? data.cites : [];
  return cites.map((item) => ({
    label: item.key,
    detail: item.kind === "bib" ? item.title || item.file_path : item.file_path,
    type: "cite",
    // bib 条目（可在文献表中解析）排前，tex 已引用 key 兜底
    boost: item.kind === "bib" ? 1 : 0,
  }));
}

/**
 * 按已敲入前缀过滤候选项：大小写不敏感的前缀命中优先、子串命中其次
 * （同分保持原序），超出上限截断。空查询给全量（截断）。
 * @param {Array<{label: string, boost?: number}>} options
 * @param {string} query
 * @returns {Array}
 */
export function filterCompletionOptions(options, query) {
  const q = (query || "").toLowerCase();
  const ranked = [];
  for (let i = 0; i < options.length; i += 1) {
    const option = options[i];
    const label = option.label.toLowerCase();
    let rank;
    if (!q || label.startsWith(q)) rank = 0;
    else if (label.includes(q)) rank = 1;
    else continue;
    ranked.push({ option, rank, order: i });
  }
  ranked.sort((a, b) => a.rank - b.rank || a.order - b.order);
  return ranked.slice(0, MAX_OPTIONS).map(({ option }) => option);
}
