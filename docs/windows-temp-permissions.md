# Windows 临时目录权限问题排查

> 适用场景：在 Windows 上使用 Codex 开发时，pytest 或其它工具写入/清理
> 临时目录报 `PermissionError: [WinError 5] 拒绝访问`。

## 现象

- pytest 启动或结束时抛出 `PermissionError`，路径指向仓库根目录下的
  `.pytest_tmp` 或其子目录；
- `Get-ChildItem` / `icacls` 访问 `.pytest_tmp` 被拒绝；
- 偶发出现在前端构建、npm 等使用系统 `%TEMP%` 的工具上。

## 根因

### 1. Codex 的 Windows 沙箱把命令运行在专用用户下

例如 `~/.codex/config.toml` 使用 `[windows] sandbox = "elevated"` 时，
Codex 启动的每条命令以专用本地用户（`CodexSandboxOffline` /
`CodexSandboxOnline`）和写受限令牌执行，而不是当前登录用户：

```powershell
whoami
# 本机示例输出：pc\codexsandboxoffline
```

可用写入范围默认限于工作区目录与 `writable_roots`。系统 `%TEMP%`
（如 `C:\Users\<用户>\AppData\Local\Temp`）是否可写取决于沙箱配置与 ACL。

### 2. Windows ACL 按主体（SID）判定权限

Windows 的目录/文件权限记录在 ACL 中，按创建者/授权主体的 SID 判定。
用户 A 创建的文件，用户 B 不一定能读取或删除。同一台机器上"真实用户跑
pytest"与"Codex 沙箱用户跑 pytest"交替执行时，创建和清理必须由同一主体完成。
跨主体清理会报 `WinError 5`。

### 3. pytest basetemp 的生命周期（旧方案）

旧方案在 `pytest.ini` 固定 `--basetemp=.pytest_tmp`：pytest 会在会话
开始时整体重建该目录。若上次会话异常中断（或由另一主体创建）导致目录
残留且 ACL 损坏，下一次会话的清理就会失败。即使换用"每会话一个目录"，
残留目录仍需可靠清理机制兜底。

## 仓库现状

- 根 `conftest.py` 为每次 pytest 会话创建独立目录
  `.pytest_tmp/session-<时间戳>-<随机串>/`，会话之间互不清理对方目录；
- 下次会话开始时尽力清理更早的历史会话目录，保留数量沿用
  `tmp_path_retention_count`（默认 3）；
- 显式传入 `--basetemp` 时以用户参数为准；
- 权限受限无法清理的残留目录由 `clean-cache.ps1` 兜底（自动提权）。

## 排查步骤

1. 确认执行主体：`whoami`（沙箱内应为 `pc\codexsandboxoffline` 之类）；
2. 确认残留目录：`Test-Path .pytest_tmp`、`Get-ChildItem .pytest_tmp -Force`；
3. 查看 ACL：`icacls .pytest_tmp`；
4. 排查占用：`Get-Process python*`，并确认没有编辑器/杀软/OneDrive
   锁定该目录；
5. 复现：`python -m pytest tests/ -q`。

## 解决方式

### 1. 一键清理（推荐）

```powershell
.\clean-cache.ps1 -NoPause
```

脚本清理 `__pycache__`、`.pytest_cache`、`.pytest_tmp` 等。`.pytest_tmp`
删除失败时会自动弹 UAC 提权重试（仅针对该目录）。

### 2. 手动修复 ACL 后删除

在真实用户（或管理员）PowerShell 中：

```powershell
$pytestTmp = Join-Path (Get-Location) ".pytest_tmp"
takeown /f $pytestTmp /r /d y
icacls $pytestTmp /reset /T /C
Remove-Item -LiteralPath $pytestTmp -Recurse -Force
```

先 `cd` 到仓库根目录再执行。若工作区不是 PowerShell 当前目录，请把
`$pytestTmp` 换成实际的 `.pytest_tmp` 路径。

### 3. 本次运行绕过残留目录

```powershell
$bt = Join-Path $env:TEMP ("pytest_" + [guid]::NewGuid().ToString("N"))
python -m pytest tests/ -q --basetemp=$bt
```

### 4. 让沙箱可写系统临时目录

在 `~/.codex/config.toml` 中把临时目录加入可写根：

```toml
[writable_roots]
"C:/Users/<你的用户名>/AppData/Local/Temp" = true
```

或把 `TEMP` / `TMP` 环境变量指向工作区内目录，让所有临时文件落在沙箱
必然可写的位置。

## 预防

- 避免在 Codex 沙箱内外交替运行 pytest 后留下跨主体目录。异常中断后及时
  清理；
- 工作区避免放在 OneDrive / 云端同步目录内，并可将工作区加入杀软排除；
- 修改 `tmp_path_retention_count` 可控制历史会话目录保留数量；
- 出现问题时按本文档先确认主体（`whoami`）再动手，避免误删。

## 参考

- OpenAI 官方工程博客：Building a safe, effective sandbox to enable Codex
  on Windows（https://openai.com/index/building-codex-windows-sandbox/）
- Codex Windows 沙箱配置与排障（learn.chatgpt.com/docs/windows/windows-sandbox）
