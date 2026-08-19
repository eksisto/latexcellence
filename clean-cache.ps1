# LateXcellence Cache Cleaner
# PowerShell 版本，与 clean-cache.sh 功能一致。

param(
    [switch]$NoPause
)

$ErrorActionPreference = "SilentlyContinue"
$Root = $PSScriptRoot
$script:Cleaned = 0
$script:Failed = 0

Write-Host ""
Write-Host "========================================"
Write-Host "  LateXcellence Cache Cleaner"
Write-Host "========================================"
Write-Host ""

function Get-CacheItems {
    param(
        [string]$SearchRoot,
        [string]$NamePattern
    )

    $found = [System.Collections.Generic.List[string]]::new()
    $pending = [System.Collections.Generic.Stack[string]]::new()
    $pending.Push($SearchRoot)

    while ($pending.Count -gt 0) {
        $current = $pending.Pop()
        $children = @(Get-ChildItem -LiteralPath $current -Force -ErrorAction SilentlyContinue)

        foreach ($child in $children) {
            if ($child.PSIsContainer -and $child.Name -eq "node_modules") {
                continue
            }
            if ($child.Name -like $NamePattern) {
                $found.Add($child.FullName)
            }
            if ($child.PSIsContainer) {
                $pending.Push($child.FullName)
            }
        }
    }

    return $found
}

function Remove-CacheItem {
    param([string]$Item)

    $relative = $Item
    if ($Item.StartsWith($Root, [System.StringComparison]::OrdinalIgnoreCase)) {
        $relative = $Item.Substring($Root.Length).TrimStart('\')
    }

    try {
        Remove-Item -LiteralPath $Item -Recurse -Force -ErrorAction Stop
        Write-Host "  [OK]   $relative"
        $script:Cleaned++
    }
    catch {
        Write-Host "  [FAIL] $relative"
        $script:Failed++
    }
}

function Invoke-CleanPattern {
    param(
        [string]$Description,
        [string]$NamePattern,
        [string]$SearchRoot
    )

    $items = @(Get-CacheItems -SearchRoot $SearchRoot -NamePattern $NamePattern)
    if ($items.Count -eq 0) {
        Write-Host "  --     $Description (不存在，跳过)"
        return
    }

    foreach ($item in $items) {
        Remove-CacheItem -Item $item
    }
}

function Remove-PytestTmp {
    param([string]$Target)

    # .pytest_tmp 是 pytest 会话级 basetemp 根目录（由根 conftest.py 为每次
    # 会话生成 session-* 独立子目录，详见 docs/windows-temp-permissions.md）。
    # 正常会话会保留最近若干次、由下次会话自动清理更早目录；若会话异常中断
    # 或跨 Codex 沙箱主体运行，会残留成普通权限无法删除的目录（连 listdir
    # 都拒绝），进而导致后续 pytest 会话无法创建新目录。这里补清理，并在
    # 普通删除失败时自动提权重试（仅该目录、仅失败时才弹 UAC）。

    if (-not (Test-Path -LiteralPath $Target)) {
        Write-Host "  --     .pytest_tmp (不存在，跳过)"
        return
    }

    $removed = $false
    try {
        Remove-Item -LiteralPath $Target -Recurse -Force -ErrorAction Stop
        $removed = $true
    }
    catch {
        Write-Host "  [FAIL] .pytest_tmp（ACL 受限或文件被占用）"
    }

    if (-not $removed) {
        $isAdmin = ([Security.Principal.WindowsPrincipal] `
            [Security.Principal.WindowsIdentity]::GetCurrent()
        ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
        if (-not $isAdmin) {
            Write-Host "  尝试以管理员权限清理 .pytest_tmp（会弹出 UAC 确认框，请选择 Yes）..."
            try {
                $elevated = "Remove-Item -LiteralPath '$Target' -Recurse -Force"
                Start-Process -FilePath "powershell" -Verb RunAs -Wait `
                    -ArgumentList "-NoProfile", "-Command", $elevated
                if (Test-Path -LiteralPath $Target) {
                    Write-Host "  [FAIL] .pytest_tmp（管理员清理仍失败）"
                }
                else {
                    $removed = $true
                }
            }
            catch {
                Write-Host "  已取消提权，.pytest_tmp 未清理（可稍后以管理员身份运行本脚本）"
            }
        }
    }

    if ($removed) {
        Write-Host "  [OK]   .pytest_tmp"
        $script:Cleaned++
    }
    else {
        $script:Failed++
    }
}

Write-Host "[1/6] 清理 __pycache__ ..."
Invoke-CleanPattern -Description "__pycache__" -NamePattern "__pycache__" -SearchRoot $Root

Write-Host "[2/6] 清理 *.pyc ..."
Invoke-CleanPattern -Description "*.pyc" -NamePattern "*.pyc" -SearchRoot $Root

Write-Host "[3/6] 清理 node_modules/.cache ..."
$cache = Join-Path $Root "frontend\node_modules\.cache"
if (Test-Path -LiteralPath $cache) {
    Remove-CacheItem -Item $cache
}
else {
    Write-Host "  --     node_modules/.cache (不存在，跳过)"
}

Write-Host "[4/6] 清理 .pytest_cache ..."
Invoke-CleanPattern -Description ".pytest_cache" -NamePattern ".pytest_cache" -SearchRoot $Root

Write-Host "[5/6] 清理网页抓取缓存 ..."
$webCache = Join-Path $Root "data\web_cache"
if (Test-Path -LiteralPath $webCache) {
    Remove-CacheItem -Item $webCache
}
else {
    Write-Host "  --     data/web_cache (不存在，跳过)"
}

Write-Host "[6/6] 清理 .pytest_tmp（pytest 会话级临时目录）..."
Remove-PytestTmp -Target (Join-Path $Root ".pytest_tmp")

Write-Host ""
Write-Host "========================================"
Write-Host "  清理结果"
Write-Host "========================================"
Write-Host ""
Write-Host "  已清理: $Cleaned 项"
Write-Host "  失败:   $Failed 项"
Write-Host ""

if (-not $NoPause) {
    Write-Host "按任意键退出..." -NoNewline
    $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
    Write-Host ""
}
