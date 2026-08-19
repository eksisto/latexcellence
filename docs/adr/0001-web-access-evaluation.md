# ADR 0001：网络访问阶段 0 使用离线评测工具

## 状态

已接受（阶段 0）。

## 背景

网络访问改进需要比较 legacy 与后续 v2，但单元测试不能访问真实搜索引擎、学术 API 或公网网页。评测数据还必须能在 Windows 开发环境中重复运行，并避免把第三方正文复制进仓库。

## 决策

阶段 0 使用 `tools/web_access_eval/` 的原生 Python + Pydantic 工具：

- 数据集和预测结果使用严格 JSON/Pydantic 契约；指标计算只依赖 Python 标准库。
- 搜索/学术集保存 URL、稳定标识符和人工相关性标签；抽取/grounding 使用项目自有 HTML fixture。
- 评测命令只做离线文件读取与确定性计算，不接入 FastAPI、SearchManager 或 Writing Chat 生产链路。
- Playwright、第二 HTML 抽取器、PDF 解析器及 `pi-webaio` 对照运行器暂不加入依赖，先完成离线 spike 和资源/许可证评估，再单独提交 ADR。

## 取舍与后续验证

不新增依赖意味着新增第三方依赖安装体积为零、Windows 支持继承 Python/Pydantic，失败回退是“不运行外部访问”而不是静默使用网络。代价是阶段 0 不能测量真实网页渲染或 PDF 抽取质量；这些能力必须在隔离的本地测试站和固定 fixture 中另行验证。

后续依赖 ADR 必须记录候选库的许可证、安装体积、Windows 支持、浏览器/进程资源占用、超时和失败回退，并在生产接入前通过安全评审。
