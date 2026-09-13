---
name: latexcellence-config-sync
description: 修改 LaTeXcellence 的 config.json / config.example.json 配置结构（新增、删除、改名、移动配置项或功能开关）时必须使用。确保配置项在设置界面、首页功能目录、后端 Pydantic 模型与文档中完整同步，避免出现「配置可写但界面改不了」「example 有键但 AppConfig 校验失败」等脱节。
---

# LaTeXcellence 配置同步

## 概述

LaTeXcellence 的配置项不是「改一个 JSON 就完事」：一个功能开关或配置结构变更，必须同步落到前端设置界面、首页功能目录和后端校验模型，缺一处就会出现用户改不了配置、或后端拒绝合法配置的脱节。本 skill 是修改配置结构时的强制检查单。

## 适用时机

- 新增 / 删除 / 改名 / 移动 `config.json` 或 `config.example.json` 的任何配置项
- 新增或移除功能开关（enabled 类布尔项）
- 修改配置项的类型、默认值或嵌套结构

仅修改某配置项的「值」（如调默认数值）不触发本 skill 全流程，但仍建议核对默认值文档。

## 同步检查单（按顺序执行）

修改配置结构时，以下位置必须同步更新：

1. **设置分区组件**：更新 `frontend/src/components/settings/` 下的对应分区组件
   （由 `SettingsDialog.jsx` 组装），确保用户可以通过界面修改新配置项。
2. **设置保存逻辑**：更新 `SettingsDialog.jsx` 的 `handleSave` 函数中
   `setEnabledFeatures` 调用，确保新增的功能开关能实时同步到全局状态。
3. **首页功能目录**：更新 `frontend/src/pages/WelcomePage.jsx` 中的
   `featureGroups`，确保新功能在首页有点线目录条目展示。
4. **后端校验模型**：更新 `backend/core/config.py` 中的 `AppConfig` 模型，
   确保 Pydantic 校验与 config.json 结构一致（嵌套结构同步建子模型）。
5. **example 与真实配置**：`config.example.json` 带注释模板与 `config.json`
   结构保持一致；部署侧存量配置由 `python scripts/sync_config.py --apply`
   补缺（只补键不覆盖值，方向绝不可反向）。
6. **文档同步**：按 `maintain-latexcellence-docs` skill 的要求，把配置变更
   反映到 `docs/` 对应文档（后端核心、模块文档、部署文档视影响面而定）。

## 验证

- 后端：`pytest tests/ -q` 中有配置模型相关测试；至少保证应用能以新配置启动。
- 一致性：新键必须同时出现在 `config.example.json` 与 `AppConfig`，缺一即脱节。

## 禁止

- 只改 `config.json` 不改 `AppConfig`（后端热重载校验会拒绝或静默丢字段）
- 只加后端字段不做前端设置入口（用户无法修改）
- 在 `__init__` 中缓存配置对象（必须方法内实时 `get_config()`，见 AGENTS.md）
