"""项目文件浏览工具集 + 段落编辑工具集。

工具列表（浏览）：
    - list_files             列出项目中的 .tex 文件
    - read_file_content      读取任意文件内容
    - get_section_structure  获取文档章节树
    - search_across_files    跨文件搜索

工具列表（编辑）：
    - edit_paragraph         修改指定段落内容
    - undo_last_edit         撤销对文件的最近修改
"""

from backend.utils.project_tools.manager import (
    ProjectToolsManager,
    get_project_tools_manager,
    reset_project_tools_manager,
)

from backend.utils.project_tools.edit_tools_manager import (
    EditToolsManager,
    get_edit_tools_manager,
    reset_edit_tools_manager,
)

__all__ = [
    "ProjectToolsManager",
    "get_project_tools_manager",
    "reset_project_tools_manager",
    "EditToolsManager",
    "get_edit_tools_manager",
    "reset_edit_tools_manager",
]
