#!/usr/bin/env python3
"""静态校验 backend/core/prompts.py 的 _PROMPTS 结构（不 import backend）。

检查项：
1. _PROMPTS 存在且为 dict[str, dict[str, str]]
2. 每个模板中的结构标签按标准顺序出现：
   <role> <task> <critical_rules> <input> <output_format> <examples> <custom_instructions>
   标签可缺席，但出现时相对顺序必须正确
3. 输出模块/键清单与字符体积，供人工核对

用法：
    python .agents/skills/maintain-latexcellence-prompts/scripts/check_prompt_structure.py
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

TAG_ORDER = (
    "role",
    "task",
    "critical_rules",
    "input",
    "output_format",
    "examples",
    "custom_instructions",
)
TAG_RE = re.compile(r"<(/?)([a-z_]+)>")

# prompts.py 顶部的共享常量，模板里以 + NAME 拼接，求值时需要解析
SHARED_NAMES = {
    "LATEX_JSON_ESCAPE_RULES",
    "BIBTEX_JSON_ESCAPE_RULES",
    "MARKER_PROTECTION_RULES",
    "JSON_ONLY_RULE",
    "NO_INVENTION_RULE",
    "GENERATION_FIDELITY_RULE",
    "UNTRUSTED_DATA_RULE",
    "CUSTOM_INSTRUCTIONS_ISOLATION_RULE",
    "TOOL_RESULT_PROTOCOL_RULE",
}


def find_prompts_path() -> Path:
    current = Path(__file__).resolve().parent
    for _ in range(6):
        candidate = current / "backend" / "core" / "prompts.py"
        if candidate.exists():
            return candidate
        current = current.parent
    raise SystemExit("[ERROR] 找不到 backend/core/prompts.py（请在仓库内运行）")


def eval_expr(node: ast.AST, constants: dict[str, str]) -> object:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, str):
            return node.value
        raise TypeError(f"不支持的常量类型: {type(node.value).__name__}")
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = eval_expr(node.left, constants)
        right = eval_expr(node.right, constants)
        if isinstance(left, str) and isinstance(right, str):
            return left + right
        raise TypeError("字符串拼接操作数不是 str")
    if isinstance(node, ast.Name):
        if node.id in constants:
            return constants[node.id]
        raise NameError(f"未解析的名称: {node.id}")
    if isinstance(node, ast.Dict):
        result: dict[object, object] = {}
        for key_node, value_node in zip(node.keys, node.values):
            if key_node is None:
                raise TypeError("不支持 ** 展开")
            result[eval_expr(key_node, constants)] = eval_expr(value_node, constants)
        return result
    if isinstance(node, ast.JoinedStr):
        raise TypeError(
            "不支持 f-string（模板里请用 {name} 占位符 + get_prompt 格式化）"
        )
    raise TypeError(f"不支持的表达式节点: {type(node).__name__}")


def collect_constants(tree: ast.Module) -> dict[str, str]:
    constants: dict[str, str] = {}
    for node in tree.body:
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value = node.value
        if value is None:
            continue
        for target in targets:
            if isinstance(target, ast.Name) and target.id in SHARED_NAMES:
                try:
                    constants[target.id] = eval_expr(value, constants)  # type: ignore[assignment]
                except (TypeError, NameError):
                    continue
    return constants


def eval_prompts(
    tree: ast.Module, constants: dict[str, str]
) -> dict[str, dict[str, str]]:
    for node in tree.body:
        target: ast.expr | None = None
        value: ast.expr | None = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            target = node.target
            value = node.value
        if isinstance(target, ast.Name) and target.id == "_PROMPTS":
            result = eval_expr(value, constants)
            if not isinstance(result, dict):
                raise TypeError("_PROMPTS 必须是 dict")
            typed: dict[str, dict[str, str]] = {}
            for module, keys in result.items():
                if not isinstance(module, str) or not isinstance(keys, dict):
                    raise TypeError("_PROMPTS 结构必须为 dict[str, dict[str, str]]")
                typed[module] = {str(k): str(v) for k, v in keys.items()}
            return typed
    raise SystemExit("[ERROR] 未找到 _PROMPTS 赋值")


def check_tags(module: str, key: str, text: str) -> list[str]:
    seen: list[str] = []
    for m in TAG_RE.finditer(text):
        if m.group(1):
            continue
        name = m.group(2)
        if name in TAG_ORDER:
            seen.append(name)
    violations: list[str] = []
    last_idx = -1
    for name in seen:
        idx = TAG_ORDER.index(name)
        if idx < last_idx:
            violations.append(
                f"{module}.{key}: 标签 <{name}> 出现在 <{TAG_ORDER[last_idx]}> 之后，"
                "违反标准顺序 <role> <task> <critical_rules> <input> <output_format> <examples> <custom_instructions>"
            )
        last_idx = max(last_idx, idx)
    return violations


def main() -> int:
    path = find_prompts_path()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    constants = collect_constants(tree)
    prompts = eval_prompts(tree, constants)

    violations: list[str] = []
    total_chars = 0
    print(f"检查 {path}（{len(prompts)} 个模块）")
    for module in sorted(prompts):
        print(f"  {module}: {len(prompts[module])} 个键")
        for key, text in sorted(prompts[module].items()):
            total_chars += len(text)
            tags = [
                m.group(2)
                for m in TAG_RE.finditer(text)
                if not m.group(1) and m.group(2) in TAG_ORDER
            ]
            print(f"    {key}: {len(text)} 字符, tags={tags}")
            violations.extend(check_tags(module, key, text))

    print(f"模板字符总量: {total_chars}")
    if violations:
        print("[FAIL] 标签顺序违规:")
        for item in violations:
            print(f"  - {item}")
        return 1
    print("[PASS] 所有模板标签顺序符合规范")
    return 0


if __name__ == "__main__":
    sys.exit(main())
