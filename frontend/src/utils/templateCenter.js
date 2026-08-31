/**
 * 模板中心纯函数工具（分类枚举 / 列表过滤排序 / 文件树构建）。
 *
 * 职责边界：
 * - 分类固定枚举与后端 TemplateCategory Literal 严格一致（paper/thesis/report/
 *   slides/resume/book/poster/notes/other），不随 UI 需求扩展；
 * - 列表过滤 / 排序 / 文件树构建均为无副作用纯函数，TemplateCenterPage 与
 *   template-center 组件共用，并可在 node --test 中直接单测
 *   （不引入 React / 路径别名依赖）。
 */

// 分类固定枚举（设计决策，不得扩展；i18n 键 pages:templateCenter.categories.<key>）
export const TEMPLATE_CATEGORIES = [
  "paper",
  "thesis",
  "report",
  "slides",
  "resume",
  "book",
  "poster",
  "notes",
  "other",
];

const CATEGORY_SET = new Set(TEMPLATE_CATEGORIES);

/** 合法分类判定（表单提交前守卫） */
export function isValidCategory(category) {
  return CATEGORY_SET.has(category);
}

/** 非法/缺失分类折叠为 other（与后端自动生成 manifest 的回退语义一致） */
export function normalizeCategory(category) {
  return isValidCategory(category) ? category : "other";
}

/** 分类 i18n 键（绑定 pages 命名空间使用） */
export function categoryLabelKey(category) {
  return `templateCenter.categories.${normalizeCategory(category)}`;
}

/**
 * 模板列表过滤：关键字（name / name_en / description 不区分大小写子串匹配）
 * + 精确分类。keyword 空白视为不过滤；category 空串表示「全部分类」。
 *
 * @param {Array<object>} templates 模板摘要列表（GET /api/templates 的 templates）
 * @param {{keyword?: string, category?: string}} [criteria]
 * @returns {Array<object>}
 */
export function filterTemplates(
  templates,
  { keyword = "", category = "" } = {},
) {
  const kw = (keyword || "").trim().toLowerCase();
  return (templates || []).filter((t) => {
    if (category && t.category !== category) return false;
    if (!kw) return true;
    const haystack = [t.name, t.name_en, t.description]
      .filter(Boolean)
      .join("\n")
      .toLowerCase();
    return haystack.includes(kw);
  });
}

/**
 * 列表排序：内置模板在前；同类内按 created_at 倒序（新入库的靠前）。
 * 不修改入参数组。
 *
 * @param {Array<object>} templates
 * @returns {Array<object>}
 */
export function sortTemplates(templates) {
  return [...(templates || [])].sort((a, b) => {
    const aBuiltin = a?.kind === "builtin";
    const bBuiltin = b?.kind === "builtin";
    if (aBuiltin !== bBuiltin) return aBuiltin ? -1 : 1;
    return String(b?.created_at || "").localeCompare(
      String(a?.created_at || ""),
    );
  });
}

// 模板内源码查看扩展名（与后端 service._VIEWABLE_SUFFIXES 白名单一致；
// 缩略图 / PDF 预览走专用二进制端点，不在本白名单内）
const VIEWABLE_SUFFIXES = [
  ".tex",
  ".sty",
  ".cls",
  ".bib",
  ".bst",
  ".bbx",
  ".cbx",
  ".clo",
  ".def",
  ".cfg",
  ".fd",
  ".dtx",
  ".ins",
  ".ltx",
  ".tikz",
  ".md",
  ".txt",
  ".json",
];

/** 模板内文件是否可在线查看源码（大小写不敏感后缀判定） */
export function isViewableTemplateFile(path = "") {
  const lower = String(path).toLowerCase();
  return VIEWABLE_SUFFIXES.some((suffix) => lower.endsWith(suffix));
}

/**
 * 由扁平相对路径列表构建文件树节点：目录在前、同层按名称排序。
 * 返回节点形如 { name, path, isDir, children }（文件节点 children 为空数组）。
 *
 * @param {Array<{path: string}|string>} files 模板详情 files 列表或纯路径数组
 * @returns {Array<object>}
 */
export function buildTemplateFileTree(files) {
  const paths = (files || [])
    .map((f) => (typeof f === "string" ? f : f?.path))
    .filter((p) => typeof p === "string" && p.length > 0);

  const root = { name: "", path: "", isDir: true, children: [] };
  for (const rawPath of paths) {
    const parts = String(rawPath).split("/").filter(Boolean);
    let cur = root;
    for (let i = 0; i < parts.length; i++) {
      const isLeaf = i === parts.length - 1;
      const name = parts[i];
      const nodePath = parts.slice(0, i + 1).join("/");
      let next = cur.children.find(
        (c) => c.name === name && c.isDir === !isLeaf,
      );
      if (!next) {
        next = {
          name,
          path: nodePath,
          isDir: !isLeaf,
          children: [],
        };
        cur.children.push(next);
      }
      cur = next;
    }
  }

  const sortNodes = (nodes) => {
    nodes.sort((a, b) => {
      if (a.isDir !== b.isDir) return a.isDir ? -1 : 1;
      return a.name.localeCompare(b.name);
    });
    for (const node of nodes) sortNodes(node.children);
  };
  sortNodes(root.children);
  return root.children;
}
