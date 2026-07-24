# 模板生成模块 `template_gen/`

根据用户的自然语言描述，通过 LLM 生成 LaTeX 文档模板。

## Pydantic 输出模型

```python
class SectionSkeleton(BaseModel):
    section: str                    # 章节标题
    suggested_content: str = ""     # 建议内容

class TemplateGenResult(BaseModel):
    template_name: str              # 模板名称
    preamble: str = ""              # 导言区代码
    body_structure: list[SectionSkeleton] = []  # 正文结构
    packages: list[str] = []        # 推荐宏包
    explanation: str = ""           # 模板说明
```

## 核心方法

| 方法 | 功能 |
| --- | --- |
| `generate_template(description)` | 异步：调用 LLM 根据描述生成模板结构 |
| `save_template(template)` | 异步：组装完整 .tex 文件并保存到 `output_dir` |
| `_assemble_template(template)` | 将模板结构组装为完整 LaTeX 源码 |

## API 路由

Prefix: `/api/template-gen`, Tag: `template_gen`

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| POST | `/generate` | 根据描述生成 LaTeX 模板 |
| POST | `/save` | 将生成的模板保存为 .tex 文件 |
