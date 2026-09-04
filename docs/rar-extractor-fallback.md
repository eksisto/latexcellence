# RAR 解压器回退层设计（unar → bsdtar）

> 选择层、bsdtar 命令构造、文件名归一化、
> 错误码 `rar_extractor_missing` 迁移与 i18n 三语言同步、双工具参数化测试
> 均已落地；真实解压集成用例按工具独立 skipif，无对应二进制的环境自动跳过。
> 实施前现状：`backend/core/multiuser/upload.py` 的 `_extract_rar()` 仅支持
> 外部 `unar`；二进制缺失时返回 400 `unar_missing`（该码已由
> `rar_extractor_missing` 取代）。
>
> **遗留验收项**：真实压缩 RAR4/RAR5 端到端样本验证（§六 fixtures 扩展）——
> 压缩流无法伪造，须以真实 WinRAR/RAR 生成小型压缩样本入库后闭环
> （手工生成与校验步骤见 `tools/rar_fixtures/make_fixtures.py` 模块说明）。
>
> **补充实测（2026-08-24）**：以无 root 方式解包 libarchive-tools 3.7.4
> 后在本机跑通全部 bsdtar 变体集成用例（含 GBK 名归一化、加密/分卷拒绝、
> 大小写冲突端到端、API 上传），store 模式管线双工具验证闭环；方法见 §六。

---

## 一、背景与动机

多用户模式的 rar 上传依赖外部解压器。Alpine 系的软件源没有 `unar` 包，且经复核**不能**改用源内 7zip：

- Alpine aports 对 `7zip` 构建时显式 `DISABLE_RAR=1`（RAR 解码引擎源自受 unRAR 许可
  限制的代码，被自由软件发行版视为 non-free），源内 7zip 只能解 store（不压缩）模式
  的 RAR；
- Debian main 源的 `7zip` 同样剥离 RAR codec（本机 Debian 13 实测：store 样例可解、
  真压缩样本报 `Unsupported Method`；完整支持需 non-free 仓库的 `7zip-rar` 插件）；
- 仅 7-zip.org 上游官方二进制自带 RAR/RAR5，但脱离发行版源管理。

可行替代是 **bsdtar（libarchive-tools 包）**：libarchive 的 RAR4/RAR5 reader 是
BSD 授权独立实现（不含 unRAR 代码），不受上述许可剥离影响，各主流发行版 main 源均
收录，macOS 系统自带。本机实测（libarchive 3.7.4）：

| 样例 | 结果 |
| --- | --- |
| store 模式正常布局（flat / top_dir / 服务块 RR） | ✓ 正确解出 |
| GBK 文件名样例 | ✓ 保留原始字节（见 §四，需归一化步骤） |
| 加密 / 分卷标志样例 | 报错拒绝（管线本就预扫先拒，行为一致） |
| 手工构造的非规范 NTFS 子块样例 | ✗ Header CRC error 被拒（见 §八 风险 1） |
| 真实压缩 RAR4 / RAR5（rar 6.24 `-m5`） | ✓ 正确解出（部署收口阶段二入库样本，见 §十） |
| 真实加密 RAR5（AES-256，`-p`） | 报错拒绝（「Unsupported block header size」不含保护关键词 → `invalid_archive`，见 §十） |
| 真实 RAR5 分卷第一卷（单独上传） | 报错拒绝（截断类错误 → `invalid_archive`） |

真实压缩 RAR 未能在本机复现验证（无 rar 生成工具；现有 fixtures 全为 store 模式），
依据为其他生产项目的公开验证记录；实施前须按 §六 用真实样本闭环。
（更新：部署收口阶段二已以 rar 6.24 生成真实压缩/加密/分卷样本入库并完成
双工具实测，事实记录见 §十。）

## 二、目标与非目标

**目标**

- rar 上传在仅有 bsdtar 的主机（Alpine/postmarketOS）可用；
- 已有安全语义零放宽、零变化：预扫拒绝、配额监视、限时击杀、零产物兜底、大小写
  重名扫描、解压后权威复核全部保留；
- 双工具并存环境（如 Debian 裸机）行为与现状完全一致（仍走 unar）。

**非目标**

- 不新增 RAR 创建/压缩能力；
- 不放宽加密包 / 分卷包拒绝口径；
- 不引入进程内解析库（保持外部子进程 + 监视管线的既有威胁模型）；
- 不做「一个包失败后换另一个解压器重试」（见 §三）。

## 三、方案总览：可用性选择层

```
extract_rar(archive_path, extract_dir, limits)   # upload_rar.py，由 upload.extract_archive 分发
    │
    ├─ resolve_rar_extractor()
    │     shutil.which("unar")     → ("unar",   _build_unar_cmd)      # 现状不变
    │     shutil.which("bsdtar")   → ("bsdtar", _build_bsdtar_cmd)    # 新增回退
    │     两者皆缺                  → ArchiveError("rar_extractor_missing", …)
    │
    └─ 后续管线与解压器无关：预扫 → Popen(命令) → watcher → 错误映射
       → 大小写重名扫描 → （bsdtar 分支额外）文件名归一化 → 零产物兜底
```

关键决策：

1. **unar 优先、bsdtar 缺失才回退**。unar 在生产已验证且容错更宽；有 unar 的环境
   行为逐字节不变，风险只暴露在原本完全不能用 rar 的环境。
2. **不跨解压器重试**。同一归档在 unar 失败后换 bsdtar 重试会引入部分解压状态下的
   配额/限时语义复杂化，且两工具对损坏包的判定不同，重试结果不可复现。rar 为低频
   上传格式，收益不抵复杂度。
3. **错误码收敛为一个新码** `rar_extractor_missing`（HTTP 400），取代
   `unar_missing`——Alpine 用户看到「请安装 unar」的文案无法执行（源里没有），文案
   改为「服务器未安装可用的 rar 解压工具（unar 或 bsdtar）」。同步清单见 §五。

## 四、bsdtar 命令构造与文件名归一化

### 命令对照

| 解压器 | 命令 | 备注 |
| --- | --- | --- |
| unar | `unar -f -q -D -o <dir> -- <archive>`（现状不变） | `-D` 保持根归一化语义 |
| bsdtar | `bsdtar -x -f <abs_archive> -C <abs_dir>` | 归档与目录都用绝对路径 |

bsdtar 要点：

- **禁止 `-P`**（会保留成员名的绝对路径语义）；不使用任何 insecure 类开关；
- `stdin=subprocess.DEVNULL`（防加密包交互提示挂起）、
  `start_new_session=True`（进程组可整体 SIGKILL）——与 unar 分支完全一致，
  watcher 与 `_kill_process_group` 原样复用；
- libarchive 默认剥离成员名前导 `/` 并对 `..` 有内建防护，但**管线不依赖此声明**：
  解压后 `_validate_extracted` 复核仍是唯一权威兜底（与 zip/7z/rar-unar 同一口径）。

### 文件名归一化（bsdtar 分支新增步骤）

**问题**：unar 自带字符集探测，产出合法 Unicode 名；libarchive 不做探测，非 UTF-8
的 RAR4 原始字节名经 Python surrogateescape 落盘（本机实测 GBK 样例落盘为
`\xd6\xd0\xce\xc4.txt`）。此类名字若直接进 JSON 响应（项目文件树接口），lone
surrogate 会使 UTF-8 序列化失败，属于必须处理的正确性问题而非体验问题。

**方案**：bsdtar 分支在进程成功退出后、大小写重名扫描前增加
`normalize_rar_names(extract_dir)`：

1. 用 `os.scandir` 以 bytes 路径**后序收集**目录树全部条目（显式
   `follow_symlinks=False`，绝不递进符号链接目录——本步骤先于解压后复核的
   符号链接拒绝执行）；命中判定：条目名经 surrogateescape 解码为 str 后，
   无法再 strict 编码回 UTF-8 即含 lone surrogate；
2. 收集结果按路径**长度降序**处理——后代路径恒比祖先长，保证深层条目先于其
   父目录被改名（等价于「自底向上」，父目录改名不会残留失效的子路径前缀）；
3. 命中者取原始字节名依次尝试解码：`utf-8`（strict）→ `gbk` → `cp437`
   （与 zip 管线的解码链一致，保持全格式语义对齐）；
4. 解码成功则用 `os.rename` 的 bytes 形式原地改名（绕过 str/surrogate 边界）；
   目标名已存在（`os.path.lexists`，不解引用符号链接）→ `invalid_archive` 拒绝；
5. 全链失败 → `invalid_archive` 拒绝（宁可拒收，不入库坏名）。

合法 UTF-8 名为 no-op；仅 bsdtar 分支调用（unar 分支无需遍历开销）。

GBK 误判风险与 zip 管线同源（GBK 字节对覆盖面广可能误还原），接受同等级已知限制。

## 五、错误码与关键词映射

### 错误码变更

| 项 | 现状 | 变更后 |
| --- | --- | --- |
| 后端产生点 | `upload.py` 抛 `unar_missing` | 改抛 `rar_extractor_missing` |
| HTTP 映射 | service 层未知稳定码默认 400 | 无需改动（新码同走默认分支） |
| 前端精确映射 | `errorMessages.js`: `unar_missing: "unarMissing"` | 改为 `rar_extractor_missing: "rarExtractorMissing"`，移除旧键 |
| i18n 三语言 | zh / zh-TW / en 各含 `unarMissing` 键 | 增 `rarExtractorMissing`（文案见下），删 `unarMissing` |

新文案：

- zh：`服务器未安装可用的 rar 解压工具（unar 或 bsdtar），无法解压 rar 压缩包`
- zh-TW：`伺服器未安裝可用的 rar 解壓工具（unar 或 bsdtar），無法解壓 rar 壓縮檔`
- en：`The server has no usable rar extractor (unar or bsdtar); rar archives cannot be extracted`

### 失败输出关键词

现有词表 `_RAR_PROTECTED_KEYWORDS = ("password", "encrypt", "volume")` 对 bsdtar
输出同样适用，**无需新词表**（按所选解压器记日志即可）：

- libarchive 对加密 RAR 的报错含 `encrypt`（RAR4 不支持加密读取）或要求 passphrase
  （RAR5 AES，stdin 关闭下必然报错）→ 命中 → `archive_unsafe` ✓；
- 差异点：RAR5 分卷缺卷时 bsdtar 报截断类错误、通常不含 `volume` → 错误码落
  `invalid_archive` 而非 `archive_unsafe`。安全性不变（仍然拒绝），仅错误细分度下降；
  RAR4 分卷仍由预扫确定性拒绝（`archive_unsafe`），RAR5 第一卷单独上传的静默形态
  由「成功但零产物」兜底覆盖。

## 六、测试计划

**单元测试（monkeypatch `shutil.which` 与 fake Popen，不依赖二进制）**

- 选择逻辑三态：有 unar / 仅 bsdtar / 全缺（断言 `rar_extractor_missing`）；
- bsdtar 参数构造断言（含「命令不含 `-P`」回归项）；
- watcher 超配额 / 超时击杀、零产物兜底、大小写重名后置扫描：现有 fake 进程用例
  参数化到两种工具；
- 关键词映射：canned stderr 样例（encrypt / password / 截断类普通错误）分别断言
  错误码；
- 归一化：GBK surrogate 名成功还原；改名冲突拒绝；合法 UTF-8 名不动；
  全链解码失败拒绝；自底向上改名顺序（父子目录同为 GBK 名）。

**真实解压集成测试**

- 现有 `@requires_unar` 用例扩展为按工具参数化的 `@requires_rar_tool`
  （skipif 条件各自独立）；无对应二进制的环境自动 skip，预扫 / 接线 / 归一化 /
  监视线程用例任何环境都跑（维持现状约定）；
- GBK 断言更新：bsdtar + 归一化路径下应还原为中文名（与 zip 管线一致，
  强于 unar 的探测结果），该断言写明依赖归一化步骤；
- **无 root 环境跑真实 bsdtar 变体**：`apt-get download libarchive-tools
  libarchive13t64 && dpkg -x '*.deb' <前缀>/`，再把 `<前缀>/usr/bin` 加入
  `PATH`、`<前缀>/usr/lib/x86_64-linux-gnu` 加入 `LD_LIBRARY_PATH` 即可，
  无需安装系统包；GBK 用例的解压目录使用带能力探测的 `raw_name_dir`
  fixture（见 §九风险 6）。

**fixtures 扩展（实施前置条件）**

- `tools/rar_fixtures/make_fixtures.py` 的纯字节构造保留（store 模式足够覆盖
  结构性用例）；压缩流无法伪造，需以真实 WinRAR/RAR 生成小型压缩样本
  （如 m5 压缩的数百字节 .tex）入库，或文档化手工生成与校验步骤；
- 实施完成的验收标准必须包含：真实压缩 RAR4 与 RAR5 各一在 bsdtar 下端到端
  上传成功。

## 七、实施清单（文件级）

| 层 | 文件 | 变更 |
| --- | --- | --- |
| 后端 | `backend/core/multiuser/upload_rar.py` | rar 子系统独立模块（自 `upload.py` 拆分）：`resolve_rar_extractor()`、命令构造注册表 `RAR_EXTRACTOR_CMD_BUILDERS`、`normalize_rar_names()`、错误码替换、日志措辞随解压器名参数化；`upload.extract_archive` 分发 .rar 时延迟导入其 `extract_rar()` |
| 前端 | `frontend/src/utils/errorMessages.js` | 映射增删（见 §五） |
| 前端 | `frontend/src/i18n/locales/{zh,zh-TW,en}.json` | errors 命名空间增删键 |
| 文档 | `docs/backend-core.md` | upload.py 行补「unar 优先、bsdtar 回退」与归一化步骤 |
| 文档 | `docs/deployment.md` | 依赖表加 `libarchive-tools`（可选，rar 上传需要其一）；故障表 `unar_missing` 行改 `rar_extractor_missing`；沙箱例外跟踪项措辞扩为「unar/bsdtar」 |
| 文档 | `docs/multi-user-v2-plan.md` §7 | 附注指向本文档 |
| 文档 | `README.md` | 多用户功能描述「rar 经 unar 解压」改为「rar 经 unar 或 bsdtar 解压」 |
| 文档 | `THIRD_PARTY_NOTICES.md` | 增加 libarchive 条目（系统包引入，BSD 许可族，不经 pip） |
| 文档 | `PROJECT_SUMMARY.md` | 多用户项目行措辞同步 |
| 镜像 | `docker/backend/Dockerfile` 及 convert 镜像跟踪项 | 后端镜像已有 unar 不动；convert 镜像未来纳入沙箱时同时内置 unar 与 bsdtar |

## 八、部署与回滚（目标设备 postmarketOS）

```bash
doas apk add libarchive-tools        # 提供 bsdtar（main 源）
# rsync 重推代码
doas systemctl restart latexcellence
```

验收：

1. 上传 store 模式 rar → 成功入库；
2. 上传真实压缩 rar（WinRAR 默认设置导出）→ 成功入库；
3. 上传改名为 `.rar` 的任意垃圾文件 → 400 `invalid_archive`；
4. 日志出现所选解压器名（`rar extractor selected: bsdtar`）。

回滚：还原代码重启即可；`libarchive-tools` 包留存无害（不被其他路径调用）。

## 九、风险与已知限制

1. **严格校验带来的拒收率差异**：畸形或非主流工具生成的归档在 bsdtar 下更易被拒
   （本机实测手工构造子块样例被 Header CRC error 拒绝）。双工具环境自动走 unar
   不受影响；仅 Alpine 这类单 bsdtar 环境暴露此差异，属可接受的降级（相比现状
   完全不可用仍是净收益）。
2. **压缩路径未本地闭环**：本机无 rar 生成工具，真实压缩样本验证留待实施阶段
   （§六 fixtures 扩展为硬性前置条件）。
3. **RAR5 分卷错误码细分度下降**：缺卷场景可能落 `invalid_archive` 而非
   `archive_unsafe`（§五），安全性不受影响。
4. **JSON 序列化安全依赖归一化步骤**：bsdtar 分支若跳过 `normalize_rar_names()`,
   surrogate 名会导致文件树接口序列化失败；实施时须配齐单测防止回归。
5. **排障心智**：两工具对同一损坏包的错误信息不同，日志需携带解压器名字段区分。
6. **原始字节名依赖 POSIX 语义文件系统**：归一化以「libarchive 把非 UTF-8 名按
   原始字节落盘」为前提，要求暂存区所在文件系统接受任意字节名（ext4/tmpfs 等
   均可）。在拒绝非法字节名的挂载点（如 ntfs3/exFAT，stat 直接 EINVAL）上，
   bsdtar 解压此类归档会以 `invalid_archive` 拒绝——安全无损的降级，但意味着
   该类环境下非 UTF-8 名 rar 不可用；测试侧 GBK 用例因此使用带能力探测的
   `raw_name_dir` fixture，避免环境性误报。

## 十、入库真实压缩样本（部署收口阶段二事实记录）

真实压缩流无法伪造，样本以 rarlab rar CLI 生成后入库 `tests/fixtures/rar/`
（自产 `.tex` 数据，不分发 rar 软件本体，总体积 KB 级）：

| 样本 | 生成要点 | 用途 |
| --- | --- | --- |
| `compressed_rar4.rar` / `compressed_rar5.rar` | `rar a -ma4/-ma5 -m5 -ep`（346 字节源 `.tex`，pack 211/211） | 真实压缩包双工具端到端上传解压 |
| `encrypted_rar5.rar` | `rar a -ma5 -m5 -ep -p<口令>`（AES-256） | 真实加密包确定性拒绝 |
| `volume4.part1.rar` / `volume4.part2.rar` | `rar a -ma4 -m5 -ep -v1k` | 真实分卷对确定性拒绝 |

结构期望与生产管线预扫行为的自动对账：`python
tools/rar_fixtures/verify_fixtures.py`（版本头、压缩方法与实际压缩比、
加密/分卷标志、成员名、单样本体积上限；环境有解压器时追加真实解压指纹
校验）。再生成步骤见 `tools/rar_fixtures/make_fixtures.py` 模块 docstring。

实测结论（rar 6.24 / unar 1.10.8 / bsdtar 3.7.4，2026-09）：

- **rar 7.x 起移除 `-ma4`**，无法再生成 RAR4 归档（Debian 源 rar 7.11 实测
  `Unknown option: ma4`）；RAR4 样本须用 rar 5.x / 6.x 生成；
- 真实压缩 RAR4 / RAR5 在 unar、bsdtar 下均端到端上传解压成功，产物与源
  内容逐字节一致（`TestRealCompressedExtract`，双工具参数化）；
- 真实加密 RAR5 的拒绝码**按工具细分**：unar 报「requires a password」
  命中关键词 → `archive_unsafe`；bsdtar 报「Unsupported block header size」
  不含关键词 → `invalid_archive`（拒绝确定性成立，细分度下降同 §五）；
- **RAR5 分卷第一卷单独上传**（隔离目录、无兄弟分卷，与上传场景一致）：
  unar 零产物退出（exit=0）→ 由「成功但零产物」兜底 `invalid_archive`
  （§五 的假设实测成立）；bsdtar 报「Too small block encountered」非零
  退出、不含保护关键词 → `invalid_archive`。两工具均确定性拒绝，但 unar
  分支依赖零产物兜底这一弱信号，且 unar 在归档同目录存在兄弟分卷时会
  自动补全解压（生产上传为单文件暂存，不触发）。分卷入库样本因此选用
  RAR4——预扫 MHD_VOLUME 在解压发起前确定性拒绝，与解压器行为完全无关；
- **SERVICE(0x7A) 服务块必须置 LONG_BLOCK（0x8000）**：手工构造缺该标志时
  unar/unrar 视为无数据区、在数据字节上瞎找下一块直至放弃（lsar 列出
  0 个成员、解出 0 个文件），仅 bsdtar/libarchive 容忍；`service_rr.rar`
  已按真实结构修复（flags=0xC000），verify 脚本对该标志有回归断言。
