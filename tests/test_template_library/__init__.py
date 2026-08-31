"""模板库模块测试（阶段②）。

覆盖内容：
    - manifest 模型校验（非法枚举 / 缺字段 / 多余字段）
    - builtin_store 发现与共享文件原语（路径穿越拒绝）
    - user_store CRUD（多用户 / 单用户双模式，用户隔离）
    - /api/templates 全端点（单用户 API）
    - 多用户 API（登录 / 越权 404 / 配额超限 413 / 清洗 / from-project /
      from-template-gen / 衍生配额并入「模板」类别）

本模块零 LLM 依赖，无需 mock LLM；异步测试统一经 TestClient 驱动。
"""
