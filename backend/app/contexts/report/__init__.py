"""
Report Context — 报告与证据管理。

职责：
- 将 Agent 分析结果生成结构化报告
- 证据文件上传 MinIO（shared.object_store）
- 报告状态机：draft → generated → published

结构：
- models.py      Report / Evidence ORM 模型
- schemas.py     API 请求响应契约
- repository.py  数据访问层
- service.py     报告生成与发布编排
- api.py         REST 端点
"""
