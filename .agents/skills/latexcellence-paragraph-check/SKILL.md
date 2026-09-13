---
name: latexcellence-paragraph-check
description: 实现或修改 LaTeXcellence 的「逐段检查」类功能时必须使用（错字查找 typo_check、表达优化 expression_opt、术语一致性 terminology_check、逐段翻译 paragraph_translate，及其前端页面、useParagraphCheck hook、结果列表交互）。约束后端 BaseParagraphCheckService 扩展方式与前端结果展示、排序、应用、持久化的固定模式，防止改坏 marker 保护、检查结果保鲜与显示顺序。
---

# LaTeXcellence 逐段检查实现模式

## 概述

逐段检查是 LaTeXcellence 的核心功能族：后端由 `BaseParagraphCheckService`
基类统一封装文件解析、marker 替换、LLM 编排与写回；前端由 `useParagraphCheck`
hook 统一封装恢复、预览、检查、排序、应用与持久化。**新建或修改此类功能必须
复用这两层，禁止在页面或 service 里重复实现流程逻辑。**

## 后端约束

- 新检查模块继承 `backend/core/base_paragraph_check.py` 的
  `BaseParagraphCheckService`，通过 hooks 定制差异（提示词、结果模型、写回策略）。
- Marker 保护：LaTeX 结构替换为 `__CMD_0000__` / `__MATH_0000__` 等 marker，
  System Prompt 必须强约束 LLM 绝对禁止修改/删除/拆分 marker；
  还原调用 `LatexParser.restore_from_markers()`，按创建顺序（counter）从大到小。
- LLM 输出必须走 `json_utils.retry_with_validation()`
  （容错解析 → Pydantic 校验 → 失败反馈重试，最多 3 次）。
- 每次检查生成 `operation_id`，保存 marker 映射与文件 MD5 快照
  （CheckOperationContext），apply 时精确还原，防止 marker 污染源文件。
- 写回前用 `latex_json_guard.check_latex_text()` 校验转义损坏。

## 前端约束

- **必须用 `useParagraphCheck` hook 封装**：恢复 / 文件选择 / 预览 / 检查优化 /
  排序 / 应用 / refresh 持久化；页面只传 API 模块与 store slice 配置，
  禁止在页面重复实现流程。
- 结果显示顺序：`changes` 非空优先，无建议在后；同类内部按 `paragraph_index`
  升序；**排序完成后再做过滤**，保持有序。
- 应用单段修改后**不得**刷新全部预览（会清空已生成结果）；从结果列表过滤掉
  已应用段落；检查结果需纳入 Zustand persist `partialize` 以支持刷新保留。
- 含 marker（如 `__MATH_0000__`）的字符串用 `<MarkerText>` 渲染友好标签；
  展开「查看原文」用 `<ParagraphContext>` 显示前后段落上下文。
- 检查结果保鲜：结果记录检查时文件 MD5，页面加载时比对，不一致渲染
  `StaleCheckBanner` 提示重查而不销毁结果。
- SSE 返回的 `data.message` 必须经 `translateError()` 翻译后再展示
  （见 `latexcellence-i18n-errors` skill）。

## 禁止

- 在页面组件里直接实现检查流程（绕过 `useParagraphCheck`）
- 逐段检查后调用「刷新全部预览」
- 让 LLM 输出或写回路径绕过 marker 保护与 JSON 校验
- 硬编码 `e.reason === "xxx" ? "中文" : e.reason` 式条件翻译
