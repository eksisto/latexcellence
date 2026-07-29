"""项目文件浏览工具集 — 让 LLM 理解完整文档结构。

工具列表：
    - list_files             列出项目中的 .tex 文件
    - read_file_content      读取任意文件内容
    - get_section_structure  获取文档章节树
    - search_across_files    跨文件搜索
"""

from backend.utils.project_tools.manager import (
    ProjectToolsManager,
    get_project_tools_manager,
    reset_project_tools_manager,
)

__all__ = [
    "ProjectToolsManager",
    "get_project_tools_manager",
    "reset_project_tools_manager",
]
