"""
Settings Context — 后台配置管理。

当前范围：LLM Provider（DeepSeek 等 Anthropic 兼容端点）。
- api_key Fernet 加密落库，列表掩码回显
- is_default 全局唯一（当前启用项），Agent 任务运行时取默认 Provider；无独立 enabled
- 测试连接真实调用 /v1/messages 验证

结构：
- models.py      LlmProvider ORM 模型
- schemas.py     请求/响应契约
- repository.py  数据访问层
- service.py     CRUD / 激活 / 测试连接 / 运行时配置解析
- api.py         REST 端点
"""
