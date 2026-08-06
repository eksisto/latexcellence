"""
latex_package_rules 规则表数据完整性测试。

守卫目标：
    - 内核白名单：KERNEL_COMMANDS / KERNEL_ENVS 与规则表无交集，
      防止 \\frac / \\mathcal / \\hline / tabular 之类内核能力误入
    - 数据完整性：同一命令/环境只映射一个宏包、包名合法（小写/无 .sty，
      xeCJK 豁免）、命令/环境名合法
    - 领域排除表：排除的宏包都必须是已知宏包（无失效条目），
      general 领域不排除任何宏包
    - 歧义决策记录：AMBIGUITY_DECISIONS 引用的命令/环境必须在规则表中存在
"""

import re

import pytest

from backend.utils.latex_package_rules import (
    ALL_KNOWN_PACKAGES,
    AMBIGUITY_DECISIONS,
    COLUMN_LETTER_PACKAGES,
    COLUMN_PREFIX_PACKAGES,
    COMMAND_PACKAGES,
    DOMAIN_EXCLUDED_PACKAGES,
    ENV_PACKAGES,
    KERNEL_COMMANDS,
    KERNEL_ENVS,
    MIXED_CASE_PACKAGE_NAMES,
)


class TestKernelWhitelist:
    """内核白名单守卫：规则表不得收录内核能力。"""

    def test_no_kernel_commands_in_rule_table(self):
        overlap = set(COMMAND_PACKAGES) & set(KERNEL_COMMANDS)
        assert overlap == set(), f"内核命令被误收录: {sorted(overlap)}"

    def test_no_kernel_envs_in_rule_table(self):
        overlap = set(ENV_PACKAGES) & set(KERNEL_ENVS)
        assert overlap == set(), f"内核环境被误收录: {sorted(overlap)}"

    def test_representative_kernel_commands_are_listed(self):
        """抽查典型内核命令确实在白名单内（保证白名单本身没有退化）。"""
        for cmd in (
            "frac",
            "sum",
            "int",
            "mathcal",
            "hline",
            "newtheorem",
            "label",
            "ref",
            "cite",
            "caption",
            "item",
            "section",
            "begin",
            "end",
        ):
            assert cmd in KERNEL_COMMANDS, f"{cmd} 应为内核命令"

    def test_representative_kernel_envs_are_listed(self):
        for env in (
            "tabular",
            "array",
            "equation",
            "figure",
            "table",
            "minipage",
            "verbatim",
            "itemize",
            "enumerate",
            "document",
        ):
            assert env in KERNEL_ENVS, f"{env} 应为内核环境"


class TestDataIntegrity:
    """规则表数据完整性。"""

    def test_command_maps_to_single_package(self):
        # dict 结构天然保证单值；此测试用于回归验证类型一致性
        for cmd, pkg in COMMAND_PACKAGES.items():
            assert isinstance(cmd, str) and cmd
            assert isinstance(pkg, str) and pkg

    def test_env_maps_to_single_package(self):
        for env, pkg in ENV_PACKAGES.items():
            assert isinstance(env, str) and env
            assert isinstance(pkg, str) and pkg

    def test_command_names_are_well_formed(self):
        for cmd in COMMAND_PACKAGES:
            assert "\\" not in cmd, f"命令名不应含反斜杠: {cmd}"
            assert not cmd.startswith(("{", "}")), f"命令名不应以花括号开头: {cmd}"
            assert re.fullmatch(r"[A-Za-z@*]+", cmd), f"命令名含非法字符: {cmd}"

    def test_env_names_are_well_formed(self):
        for env in ENV_PACKAGES:
            assert "{" not in env and "}" not in env, f"环境名不应含花括号: {env}"
            assert "\\" not in env, f"环境名不应含反斜杠: {env}"
            assert env.strip(), f"环境名不应为空: {env!r}"

    def test_package_names_lowercase_no_sty(self):
        for pkg in ALL_KNOWN_PACKAGES:
            assert pkg == pkg.strip(), f"包名不应含空白: {pkg!r}"
            assert not pkg.endswith(".sty"), f"包名不应带 .sty 后缀: {pkg}"
            assert "{" not in pkg and "}" not in pkg, f"包名不应含花括号: {pkg}"
            if pkg not in MIXED_CASE_PACKAGE_NAMES:
                assert pkg == pkg.lower(), (
                    f"包名应为小写（{pkg} 不在豁免清单）; "
                    f"如需大小写敏感包名，请加入 MIXED_CASE_PACKAGE_NAMES"
                )

    def test_column_letters_are_single_uppercase_char(self):
        for letter in COLUMN_LETTER_PACKAGES:
            assert len(letter) == 1, f"列说明符应为单字符: {letter!r}"
            assert letter.isupper(), f"列说明符应为大写: {letter!r}"

    def test_column_prefixes_are_well_formed(self):
        for prefix in COLUMN_PREFIX_PACKAGES:
            assert (
                prefix.endswith("{") and len(prefix) == 2
            ), f"列前缀格式非法: {prefix!r}"

    def test_ambiguity_decisions_reference_existing_rules(self):
        for name, kind, pkg, reason in AMBIGUITY_DECISIONS:
            assert reason, f"歧义决策缺少理由: {name}"
            if kind == "command":
                assert name in COMMAND_PACKAGES, f"歧义记录引用未知命令: {name}"
                assert (
                    COMMAND_PACKAGES[name] == pkg
                ), f"歧义记录与规则表不一致: {name} → {pkg} != {COMMAND_PACKAGES[name]}"
            elif kind == "environment":
                assert name in ENV_PACKAGES, f"歧义记录引用未知环境: {name}"
                assert (
                    ENV_PACKAGES[name] == pkg
                ), f"歧义记录与规则表不一致: {name} → {pkg} != {ENV_PACKAGES[name]}"
            else:
                pytest.fail(f"未知歧义类型: {kind!r} ({name})")


class TestDomainExclusions:
    """领域排除表完整性。"""

    def test_valid_domains(self):
        assert set(DOMAIN_EXCLUDED_PACKAGES) == {"general", "math", "table"}

    def test_general_excludes_nothing(self):
        assert DOMAIN_EXCLUDED_PACKAGES["general"] == frozenset()

    def test_excluded_packages_are_known(self):
        """排除表里的每个宏包都必须在规则表/隐式产出中存在（防失效条目）。"""
        for domain, excluded in DOMAIN_EXCLUDED_PACKAGES.items():
            if domain == "general":
                continue
            unknown = {p for p in excluded if p not in ALL_KNOWN_PACKAGES}
            assert (
                unknown == set()
            ), f"domain={domain} 排除表中存在未知宏包: {sorted(unknown)}"

    def test_math_excludes_table_packages(self):
        """公式 OCR 场景的关键验收：表格类宏包在 math 领域被排除。"""
        math_excluded = DOMAIN_EXCLUDED_PACKAGES["math"]
        for pkg in ("booktabs", "makecell", "colortbl", "tabularray", "longtable"):
            assert pkg in math_excluded, f"{pkg} 应在 math 领域排除"

    def test_table_keeps_math_and_table_layout(self):
        """表格 OCR 场景：数学与表格浮动宏包应保留（表格内可含公式/跨页表）。"""
        table_excluded = DOMAIN_EXCLUDED_PACKAGES["table"]
        for pkg in ("amsmath", "booktabs", "float", "wrapfig", "rotating", "siunitx"):
            assert pkg not in table_excluded, f"{pkg} 不应在 table 领域排除"
