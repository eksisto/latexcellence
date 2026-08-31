# builtin_templates（内置模板维护者工具）

内置 LaTeX 模板的编译验证与元资产生成脚本（维护者专用，不进入运行时）。

## verify_templates.py

遍历 `backend/modules/template_library/builtin_templates/` 下的全部模板，
按各模板 `manifest.json` 声明的引擎（`xelatex` / `pdflatex`）用 `latexmk`
在临时目录中编译验证，报告每个模板的成功/失败与耗时。

模板会先复制到临时目录（剔除 `assets/` 与 `manifest.json`）再编译，
用于模拟「从模板创建项目」时的干净文件树，同时避免编译产物污染源码树。

```bash
# 验证全部模板（退出码：全部成功为 0，任一失败为 1）
python tools/builtin_templates/verify_templates.py

# 验证并生成元资源：把编译 PDF 回填为 assets/preview.pdf，
# 并用 pdftoppm 渲染首页为宽约 800px 的 assets/thumbnail.png
python tools/builtin_templates/verify_templates.py --gen-assets

# 只验证/生成单个模板（调试用）
python tools/builtin_templates/verify_templates.py --only thesis-cn
python tools/builtin_templates/verify_templates.py --only paper-en --gen-assets
```

## 依赖

- TeX Live（`latexmk` / `xelatex` / `pdflatex`）；非交互 shell 下脚本会通过
  `tools/tex_path.py` 自动把常见 TeX Live 安装目录加入 `PATH`
  （也可用环境变量 `TEXLIVE_BIN` 显式指定）
- `--gen-assets` 额外依赖 `pdftoppm`（poppler-utils）

## 何时需要重跑

- 修改任何模板的 LaTeX 源文件或 `manifest.json` 之后
- 新增内置模板之后（新目录需包含 `manifest.json` + LaTeX 工程文件，
  `id` 与目录名一致）
- 升级 TeX Live 或更换编译引擎之后
