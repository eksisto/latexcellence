"""模板库模块（内置模板 + 用户私有模板库）。

- builtin_store.py：内置模板发现（扫描 builtin_templates/ 读 manifest.json）
- user_store.py：用户模板库 CRUD（多用户按用户空间隔离 / 单用户全局目录）
- service.py：业务编排（列表合并、上传建库、创建项目、另存为、配额接线）
- router.py：薄路由，挂载 /api/templates

零 LLM 依赖；manifest 结构与阶段①内置模板精确一致（builtin_templates/*/manifest.json）。
"""
