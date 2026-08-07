/**
 * 公式行内/行间包裹重写工具（与后端 formula_ocr/service.py 的
 * `_strip_math_wrappers` / `_wrap_latex_for_paste` 语义保持一致）。
 *
 * 后端识别结果可能带宏包注释前缀（如 `% \usepackage{amsmath}`），
 * 重写包裹时注释必须原样保留，否则 `\[...\]` 会被包进 `$...$` 生成非法 LaTeX。
 */

// 自带显示模式的顶层环境：切换时不改变其包裹形式
const STANDALONE_DISPLAY_ENV_RE =
  /^\s*\\begin\{(equation|equation\*|displaymath|align|align\*|alignat|alignat\*|flalign|flalign\*|gather|gather\*|multline|multline\*|eqnarray|eqnarray\*)\}/;

// 仅剥离"空壳显示环境"（不携带公式结构），结构型环境（aligned/cases/matrix/align* 等）保留
const WRAPPER_ENVS = [
  ["\\begin{equation*}", "\\end{equation*}"],
  ["\\begin{equation}", "\\end{equation}"],
  ["\\begin{displaymath}", "\\end{displaymath}"],
];

const PACKAGE_COMMENT_RE = /^%\s*\\usepackage\{/;

/**
 * 分离前置的宏包注释行（`% \usepackage{...}`）与公式主体。
 * 只有连续出现在开头的宏包注释被视为前缀，公式内部内容不受影响。
 */
function splitPackageComments(latex) {
  const lines = latex.split("\n");
  let i = 0;
  while (i < lines.length && PACKAGE_COMMENT_RE.test(lines[i])) {
    i += 1;
  }
  return { comments: lines.slice(0, i), body: lines.slice(i).join("\n") };
}

/** 剥离数学包裹（$$ / \[ \] / \( \) / $ / 空壳显示环境）。 */
export function stripMathWrappers(latex) {
  let s = latex.trim();
  if (s.length >= 4 && s.startsWith("$$") && s.endsWith("$$")) {
    s = s.slice(2, -2).trim();
  }
  if (s.length >= 4 && s.startsWith("\\[") && s.endsWith("\\]")) {
    s = s.slice(2, -2).trim();
  }
  if (s.length >= 4 && s.startsWith("\\(") && s.endsWith("\\)")) {
    s = s.slice(2, -2).trim();
  }
  if (s.length >= 2 && s.startsWith("$") && s.endsWith("$")) {
    s = s.slice(1, -1).trim();
  }
  for (const [begin, end] of WRAPPER_ENVS) {
    if (
      s.length >= begin.length + end.length &&
      s.startsWith(begin) &&
      s.endsWith(end)
    ) {
      s = s.slice(begin.length, -end.length).trim();
      break;
    }
  }
  return s;
}

/** 按行内/行间重写公式包裹，宏包注释前缀原样保留。 */
export function wrapLatexForPaste(latex, inline) {
  const raw = latex.trim();
  if (!raw) return raw;
  const { comments, body } = splitPackageComments(raw);
  const s = body.trim();
  if (!s) return raw;
  const prefix = comments.length > 0 ? `${comments.join("\n")}\n` : "";
  if (STANDALONE_DISPLAY_ENV_RE.test(s)) return prefix + s;
  const inner = stripMathWrappers(s);
  const wrapped = inline ? `$${inner}$` : `\\[${inner}\\]`;
  return prefix + wrapped;
}
