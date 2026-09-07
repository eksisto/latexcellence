# RAR 解压器回退层设计（unar → bsdtar）

> 状态：已实施（多用户 rar 上传的解压器子系统）。选择层、bsdtar 命令构造、文件名归一化、
> 错误码与 i18n、双工具参数化测试均已落地；部署收口阶段一起解压命令经 SandboxRunner 在
> convert 镜像内执行。整体管线与模块职责见 [backend-core.md](backend-core.md)「多用户模式
> 基础设施」`upload_rar.py` 行。

## 一、背景与动机

多用户模式的 rar 上传依赖外部解压器，而主流发行版源内没有理想的 RAR 工具：

- **源内 7zip 不可用**：Alpine aports 与 Debian main 对 `7zip` 构建时剥离 RAR codec
  （`DISABLE_RAR=1`，RAR 解码引擎源自受 unRAR 许可限制的代码），只能解 store（不压缩）
  模式的 RAR；完整支持需 non-free 仓库的 `7zip-rar` 插件或 7-zip.org 上游二进制（脱离
  发行版源管理）。
- **bsdtar（libarchive-tools）是可行替代**：libarchive 的 RAR4/RAR5 reader 是 BSD 授权
  独立实现（不含 unRAR 代码），不受上述许可剥离影响，各主流发行版 main 源均收录，macOS
  系统自带。
- **unar 优先**：unar 在生产已验证且自带字符集探测、容错更宽；bsdtar 仅在 unar 缺失的
  环境（如 Alpine/postmarketOS）作为回退。

## 二、目标与非目标

**目标**：rar 上传在仅有 bsdtar 的主机可用；已有安全语义零放宽（预扫拒绝、配额监视、
限时中断、零产物兜底、大小写重名扫描、解压后权威复核全部保留）；双工具并存环境行为与
历史上 unar-only 时期一致（仍走 unar）。

**非目标**：不新增 RAR 创建能力；不放宽加密/分卷拒绝口径；不引入进程内解析库（保持外部
命令 + 监视的威胁模型）；**不做「一个包失败后换另一个解压器重试」**——部分解压状态下的
配额/限时语义会复杂化，且两工具对损坏包判定不同、重试结果不可复现，rar 为低频格式收益
不抵复杂度。

## 三、方案总览：可用性选择层

```
extract_rar(archive_path, extract_dir, limits)   # upload_rar.py
    │
    ├─ 解压器选择
    │     local 后端：resolve_rar_extractor() 宿主 PATH 探测
    │       shutil.which("unar")   → unar 命令构造
    │       shutil.which("bsdtar") → bsdtar 命令构造
    │       两者皆缺 → 400 rar_extractor_missing
    │     docker 后端：不探测容器内 PATH，按 unar 优先固定顺序取注册表 builder
    │       （镜像保证两者齐备）；解压经 SandboxRunner 执行
    │       （ExecSpec：cwd=解压目录、压缩包只读挂载、timeout=既有 deadline）
    │
    └─ 后续管线与解压器无关：RAR4 头链预扫 → 执行 → 宿主监视（超配额/超时
       cancel_event 硬中断）→ 错误映射 → 大小写重名扫描
       → （仅 bsdtar 分支）文件名归一化 → 零产物兜底 → 安全复核与配额记账
```

命令构造注册表 `RAR_EXTRACTOR_CMD_BUILDERS` 集中管理两工具的参数（unar 禁交互、bsdtar
禁 `-P`），测试可用强制工具覆写钩子参数化。

## 四、bsdtar 命令构造与文件名归一化

### 命令对照

| 解压器 | 命令 | 要点 |
| --- | --- | --- |
| unar | `unar -f -q -D -o <dir> -- <archive>` | `-D` 保持根归一化语义 |
| bsdtar | `bsdtar -x -f <abs_archive> -C <abs_dir>` | 归档与目录用绝对路径；**禁 `-P`**（保留成员绝对路径语义）；stdin 接 DEVNULL 防加密包交互挂起 |

libarchive 默认剥离前导 `/` 并对 `..` 有内建防护，但管线**不依赖此声明**：解压后
`_validate_extracted` 复核仍是唯一权威兜底（与 zip/7z/rar-unar 同一口径）。

### 文件名归一化（bsdtar 分支）

**问题**：unar 自带字符集探测，产出合法 Unicode 名；libarchive 不做探测，非 UTF-8 的
RAR4 原始字节名经 Python surrogateescape 落盘。此类名字直接进 JSON 响应（文件树接口）
时 lone surrogate 会使 UTF-8 序列化失败，属正确性问题。

**方案**：bsdtar 分支在进程成功退出后、大小写重名扫描前执行 `normalize_rar_names()`：

1. `os.scandir` 以 bytes 路径**后序收集**目录树全部条目（`follow_symlinks=False`，绝不
   递进符号链接目录）；命中判定：surrogateescape 解码为 str 后无法 strict 编码回 UTF-8；
2. 收集结果按路径**长度降序**处理（后代先于祖先改名，等价自底向上）；
3. 命中者取原始字节名依次尝试 `utf-8`（strict）→ `gbk` → `cp437` 解码（与 zip 管线
   解码链一致）；
4. 解码成功则 bytes 形式 `os.rename` 原地改名；目标名已存在（`lexists`，不解引用符号
   链接）→ `invalid_archive` 拒绝；
5. 全链失败 → `invalid_archive` 拒绝（宁可拒收，不入库坏名）。

合法 UTF-8 名为 no-op；仅 bsdtar 分支调用（unar 分支无遍历开销）。GBK 误判风险与 zip
管线同源（GBK 字节对覆盖面广可能误还原），接受同等级已知限制。

## 五、错误码与关键词映射

- 缺解压器：`rar_extractor_missing`（400），文案「服务器未安装可用的 rar 解压工具
  （unar 或 bsdtar）」——早期独立码 `unar_missing` 已被取代（Alpine 用户无法按「请安装
  unar」执行）。三语条目 `rarExtractorMissing`。
- 保护类输出关键词 `password / encrypt / volume` 对两工具通用：加密 RAR4 / RAR5 AES
  报错命中 → `archive_unsafe`。
- **按工具细分差异**（安全性不变，仅错误细分度下降）：
  - RAR5 分卷缺卷：bsdtar 报截断类错误、不含 `volume` → 落 `invalid_archive`；RAR4
    分卷由预扫确定性拒绝（`archive_unsafe`）；unar 零产物退出（exit=0）→ 由「成功但
    零产物」兜底落 `invalid_archive`；
  - 真实加密 RAR5：unar「requires a password」命中关键词 → `archive_unsafe`；bsdtar
    「Unsupported block header size」不含关键词 → `invalid_archive`。
- 日志携带所选解压器名字段（两工具对同一损坏包的错误信息不同，排障需区分）。

## 六、测试布局与 fixtures

- 单元测试（`tests/test_multiuser/test_upload_rar.py`）：Mock SandboxRunner / fake 进程，
  不依赖二进制——选择逻辑三态、命令构造断言（含「不含 `-P`」回归项）、监视中断、零产物
  兜底、大小写扫描、关键词映射、归一化（GBK 还原 / 冲突拒绝 / 自底向上顺序）；
- 真实解压集成用例按工具参数化，各自独立 skipif（无对应二进制自动跳过）；docker 真容器
  冒烟见 `tests/test_docker/test_rar_sandbox_smoke.py`（marker `docker`）；
- fixtures（`tests/fixtures/rar/` + `tools/rar_fixtures/`）：store 模式结构样例由
  `make_fixtures.py` 纯字节构造；真实压缩流无法伪造，压缩样本以 rarlab rar CLI 生成入库，
  `verify_fixtures.py` 对全部样本做结构特征与生产管线预扫行为的自动对账。再生成步骤见
  `make_fixtures.py` 模块 docstring。

## 七、风险与已知限制

1. **拒收率差异**：畸形或非主流工具生成的归档在 bsdtar 下更易被拒（如手工构造的非规范
   子块样例被 Header CRC error 拒绝）。双工具环境自动走 unar 不受影响；仅单 bsdtar
   环境暴露此差异，相比完全不可用仍是净收益。
2. **RAR5 分卷错误码细分度下降**（§五）。
3. **JSON 序列化安全依赖归一化步骤**：bsdtar 分支若跳过 `normalize_rar_names()`，
   surrogate 名会导致文件树接口序列化失败；单测已防回归。
4. **原始字节名依赖 POSIX 语义文件系统**：归一化以「libarchive 把非 UTF-8 名按原始字节
   落盘」为前提，要求暂存区文件系统接受任意字节名（ext4/tmpfs 均可）。拒绝非法字节名的
   挂载点（ntfs3/exFAT，stat 直接 EINVAL）上，此类归档以 `invalid_archive` 拒绝——安全
   无损的降级；测试侧 GBK 用例使用带能力探测的 `raw_name_dir` fixture 避免环境性误报。

## 八、入库真实压缩样本与实测结论

真实样本（rarlab rar 6.24 生成，自产数据 KB 级，不分发软件本体）：

| 样本 | 生成要点 | 用途 |
| --- | --- | --- |
| `compressed_rar4.rar` / `compressed_rar5.rar` | `rar a -ma4/-ma5 -m5 -ep` | 真实压缩包双工具端到端解压 |
| `encrypted_rar5.rar` | `-p<口令>`（AES-256） | 真实加密包确定性拒绝 |
| `volume4.part1.rar` / `volume4.part2.rar` | `-v1k` | 真实分卷对确定性拒绝（选 RAR4：预扫 MHD_VOLUME 在解压前确定性拒绝，与解压器行为无关） |

实测结论（维护时仍适用的硬知识）：

- **rar 7.x 起移除 `-ma4`**（`Unknown option: ma4`），RAR4 样本必须用 rar 5.x / 6.x 生成；
- 真实压缩 RAR4/RAR5 在 unar、bsdtar 下均端到端解压成功，产物逐字节一致；
- **手工构造 RAR4 时 SERVICE(0x7A) 服务块必须置 LONG_BLOCK（0x8000）**：缺该标志时
  unar/unrar 视为无数据区、解不出任何文件（静默坏包），仅 bsdtar/libarchive 容忍；
  `service_rr.rar` 已按真实结构修复，verify 脚本对该标志有回归断言；
- unar 在归档同目录存在兄弟分卷时会自动补全解压——生产上传为单文件暂存不触发，但构造
  分卷测试样例时须隔离目录。
