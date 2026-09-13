---
name: latexcellence-commit-message
description: 在仓库提交代码（git commit）、撰写或修改 commit message 时必须使用。约束 commit message 的统一格式 `<type>(<scope>): <中文描述>`：type 取值、scope 影响范围、中文描述要求，以及正反示例。凡是要写提交说明，都先读本 skill。
---

# LaTeXcellence Commit Message 规范

## 概述

本仓库 commit message 采用统一的 `<type>(<scope>): <中文描述>` 格式。
目的：`git log` 可按 type/scope 检索过滤，一眼看出每笔提交的影响面；
描述统一用中文，与项目文档语言一致，方便团队成员扫读。

## 格式

```
<type>(<scope>): <中文描述>

<可选正文：`- ` 列表，逐条详细说明>
```

主题行与正文之间必须空一行（否则部分 git 工具会把正文并入主题行展示）。

- **type**（必填）：标识改动性质，常用取值见下表。
- **scope**（可选）：标识影响范围，帮助快速定位，如 `frontend`、`backend`、`config`、`compile`；跨多个模块时可省略，或按实际影响面取值（仓库历史中也有 `ci`、`test` 等用法）。
- **描述**（必填）：用中文，简洁明了，一行说清"做了什么"，避免笼统表述。

### type 取值

- `feat`：新功能
- `fix`：修 bug
- `refactor`：重构（不改行为的整理）
- `docs`：文档

上表为最高频取值；不够用时可选其它通用 type（如 `chore`、`test`、`ci`），保持与仓库既有提交风格一致。

## 正文（详细说明）

主题行一句话讲不清时，空一行后用 `- ` 列表写正文逐条补充。需要正文的典型场景：

- 一笔提交包含多处改动，需要逐处交代
- 有实现取舍或动机要说明（为什么这么改）
- 有值得留档的数据对比（性能前后值、参数调整）
- 行为变更影响面大，需要提示协作者

写法：

- **每条讲一处改动**：改了什么 + 为什么
- **语言**：同样用中文；代码标识符、配置字面量、命令保持原样
- 主题行保持一行讲完摘要，摘要可自带冒号列举（见下例），但不要把正文细节堆进主题行
- 一句话能讲清的小改动不写正文，不要为了凑格式硬编内容

## 示例

只有主题行（一句话能讲清的改动）：

```
feat: 多工作区支持 + 配置清理
fix(compile): 修复 latexmk 超时后子进程泄漏问题
refactor: 消除外部模块对私有属性的直接访问
```

带正文（多处改动，逐条交代改了什么、为什么、效果）：

```
fix(ci): 治理 python-check 超时：pip 默认 TUNA 镜像、超时 60m、pytest 并行

- check_backend.sh --install 与 eval job before_script 以
  ${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple} 形式默认
  走清华镜像，显式配置过 PIP_INDEX_URL 时不覆盖
- python-check timeout 30m→60m，慢速共享 runner 上的余量
- check_backend.sh 引入 pytest-xdist 并行（-n auto --dist loadfile
  --maxprocesses=8）：本地全量 197s→76s；xdist worker 跳过 .pytest_tmp
  旧会话目录清理（keep=3 < worker 数会并发误删），清理由主进程统一负责
```

不符合规范（及原因）：

```
update 代码               ← 缺 type，描述非中文且笼统
fix: 修复问题             ← 描述没有信息量，看不出修了什么
feat frontend 新功能      ← type 与描述之间缺冒号，不符合格式
docs: update README       ← 描述混用英文
```

## 禁止

- 省略 type，或使用与改动性质不符的 type（重构写成 `feat`、修 bug 写成 `chore`）
- 描述使用英文或中英混杂（仓库约定描述用中文）
- 描述笼统到无法区分多笔提交（如"修改代码""一些更新"）
- 一笔提交混入多个不相关改动、只用一个 type 概括：应拆分提交，各自按性质选 type
- commit message 中使用破折号（——）
- 主题行与正文之间不空行
- 正文堆成不分条的大段，或只罗列改了哪些文件而不讲原因
- 把细节塞进主题行，主题行冗长难扫读
