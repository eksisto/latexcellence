---
name: latexcellence-i18n-errors
description: 在 LaTeXcellence 中新增或修改后端错误消息、SSE 错误事件或 API 错误字段时必须使用。约束「后端英文错误 → 前端当前语言展示」的统一翻译链路：translateError() 调用点、三语 errors 命名空间条目、精确匹配与模式匹配的 key 规范，并禁止硬编码中文兜底。
---

# LaTeXcellence 错误消息翻译规范

## 概述

后端错误消息统一用英文（便于日志与匹配），前端**必须**翻译为当前语言后再展示。
翻译链路的唯一事实源是 `frontend/src/utils/errorMessages.js`（精确映射）+
`errorPatterns.js`（模式匹配）+ i18n 三语 `errors` 命名空间；
**禁止**在组件里写 `e.reason === "xxx" ? "中文" : e.reason` 式条件兜底。

## 翻译链路调用点

| 场景 | 是否需要手动翻译 | 说明 |
| --- | --- | --- |
| HTTP 请求错误 | 否 | `api/index.js` 的 `request()` 已统一走 `translateError()` |
| SSE 流错误事件 | **是** | `useParagraphCheck` / `useWritingChat` 等 hook 中 `data.message` 必须手动调用 `translateError()` |
| 响应体嵌套错误字段 | **是** | 如 `result.errors[].reason` 不经过 `request()` 拦截，消费处手动 `translateError()` |

## 新增后端错误时

必须在三个语言文件中同步添加 `errors` 命名空间条目：

- `frontend/src/i18n/locales/zh.json`
- `frontend/src/i18n/locales/zh-TW.json`
- `frontend/src/i18n/locales/en.json`

Key 规范：

- **精确匹配**：扁平 key，与后端错误码/reason 字面量一致（如
  `"file_conflict"`）。
- **模式匹配**：后端消息含动态部分的，用描述性 key + `{{变量}}` 占位符，
  并在 `errorPatterns.js` 的 `PATTERN_MAP` 登记正则。

## 验证

- 前端单测含 i18n 覆盖检查（`npm test` 中的 i18nCoverage），新增错误码漏配
  三语条目会直接失败。
- 改完跑：`cd frontend && npm test`。

## 禁止

- 组件内硬编码中文兜底或条件翻译
- 只加 zh.json 不加 zh-TW.json / en.json
- 让未翻译的英文错误消息直达用户界面
