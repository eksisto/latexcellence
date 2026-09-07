"""
marker 编号机制演示。

运行: pytest tests/test_marker_numbering_demo.py -v -s

你可以修改 ``latex_paragraph`` 变量中的内容来测试自己的段落。
"""

import re

from backend.utils.latex_parser import LatexParser

latex_paragraph = r"""
% ================================================================
%  main.tex —— 主文件
%
%  这个文件的作用：
%    1. 声明文档类型（文章还是报告等）
%    2. 加载需要用到的宏包（功能插件）
%    3. 用 \input{} 把各个章节的子文件拼在一起
%
%  编译时只需要编译 main.tex 这一个文件，
%  LaTeX 会自动把子文件的内容合并进来。
% ================================================================
"""

# ── 测试 ──


MARKER_RE = re.compile(r"__(CMD|MATH|COM|ENV|VERB)_(\d{4,})__")


def test_marker_numbering_demo():
    parser = LatexParser()
    stripped, marker_map = parser.strip_to_plain_text(latex_paragraph)

    markers_in_text = [
        "__" + p + "_" + n + "__" for p, n in MARKER_RE.findall(stripped)
    ]

    print("=" * 70)
    print("【原始 LaTeX 段落】")
    print(latex_paragraph)
    print("-" * 70)
    print("【Stripped 文本（含 marker）】")
    print(stripped)
    print("-" * 70)
    print("【Marker 映射表】")
    for m, v in sorted(marker_map.items()):
        print(f"  {m}  →  {v!r}")
    print("-" * 70)
    print("【Marker 在文本中的出现顺序（左到右）】")
    for i, m in enumerate(markers_in_text, 1):
        print(f"  [{i}] {m}")
    print("-" * 70)

    # 分析编号规律
    def _parse_marker(m):
        return re.match(r"__(CMD|MATH|COM|ENV|VERB)_(\d{4,})__", m).groups()

    prefixes = set(_parse_marker(m)[0] for m in marker_map)
    print("【各类 Marker 的编号范围】")
    for p in sorted(prefixes):
        nums = [int(_parse_marker(m)[1]) for m in marker_map if m.startswith(f"__{p}_")]
        if nums:
            missing = []
            for n in range(min(nums), max(nums) + 1):
                if n not in nums:
                    missing.append(f"{p}_{n:04d}")
            print(f"  {p}: min={min(nums):04d}, max={max(nums):04d}, 共{len(nums)}个")
            if missing:
                print(f"    缺失编号: {', '.join(missing)}")
            print(
                f"    文本位置索引: {[i for i, m in enumerate(markers_in_text, 1) if m.startswith(f'__{p}_')]}"
            )

    print("=" * 70)

    # 检查：编号不连续是因为其他前缀的 marker 占用了中间编号
    all_nums = sorted([int(_parse_marker(m)[1]) for m in marker_map])
    print("【全局编号连续性检查】")
    missing_global = []
    for n in range(0, max(all_nums) + 1):
        if n not in all_nums:
            missing_global.append(f"_{n:04d}")
    if missing_global:
        print(
            f"  缺失编号: {', '.join(missing_global)}（说明编号不连续，被其他前缀占用）"
        )
    else:
        print(f"  编号 0000~{max(all_nums):04d} 全部连续（无缺失）")
    print(f"  最后一个编号: {max(all_nums):04d}")


def test_marker_numbering_gap_demo():
    """演示：为什么 CMD 编号不连续 + 为什么 COM 不从 0000 开始。"""
    parser = LatexParser()
    stripped, marker_map = parser.strip_to_plain_text(latex_paragraph)

    markers_in_text = [
        "__" + p + "_" + n + "__" for p, n in MARKER_RE.findall(stripped)
    ]
    cmds = [(i, m) for i, m in enumerate(markers_in_text, 1) if m.startswith("__CMD_")]
    coms = [(i, m) for i, m in enumerate(markers_in_text, 1) if m.startswith("__COM_")]

    print("-" * 70)
    print("【Q1 分析：CMD 编号为什么不连续？】")

    def _parse_marker(m):
        return re.match(r"__(CMD|MATH|COM|ENV|VERB)_(\d{4,})__", m).groups()

    cmd_nums = [int(_parse_marker(m)[1]) for _, m in cmds]
    print(f"  CMD 编号列表: {cmd_nums}")
    if len(cmd_nums) > 1:
        for i in range(1, len(cmd_nums)):
            if cmd_nums[i] != cmd_nums[i - 1] + 1:
                gap_start = cmd_nums[i - 1] + 1
                gap_end = cmd_nums[i] - 1
                gap_types = set()
                for m in marker_map:
                    pref, n_str = _parse_marker(m)
                    n = int(n_str)
                    if gap_start <= n <= gap_end:
                        gap_types.add(pref)
                print(
                    f"  → {cmd_nums[i-1]:04d} 和 {cmd_nums[i]:04d} 之间缺了 {gap_start:04d}~{gap_end:04d},"
                )
                print(f"    这些编号被以下前缀占用: {gap_types}")
                for t in sorted(gap_types):
                    examples = sorted(
                        m
                        for m in marker_map
                        if m.startswith(f"__{t}_")
                        and gap_start <= int(_parse_marker(m)[1]) <= gap_end
                    )
                    for ex in examples[:3]:
                        v = marker_map[ex][:60]
                        print(f"      {ex} = {v!r}")

    print("-" * 70)
    print("【Q2 分析：COM 编号为什么不是从 0000 开始的？】")
    if coms:
        first_com = coms[0][1]
        first_com_num = int(_parse_marker(first_com)[1])
        print(f"  第一个 COM marker 编号是 {first_com_num:04d}")
        if first_com_num > 0:
            for n in range(0, first_com_num):
                for m in marker_map:
                    if int(_parse_marker(m)[1]) == n:
                        print(f"  编号 {n:04d} 被 {m} 占用 → {marker_map[m][:60]!r}")
                        break
            print(
                f"  → COM 不从 0000 开始的原因是前 {first_com_num} 个编号被其他前缀占用"
            )
    print("-" * 70)
