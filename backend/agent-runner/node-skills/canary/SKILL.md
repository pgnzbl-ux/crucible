# Provider Agent 兼容性测试

这是平台受控 canary，不是代码开发任务。必须严格按顺序完成，不能省略工具调用：

1. 使用 `Read` 读取输入 `marker_path` 指向的文件，保留完整原文；
2. 使用 `Bash` 执行输入 `probe_command`，只读取其 JSON 布尔结果；
3. 调用 `mcp__crucible__submit_result` 提交结果。

只有第 3 步工具调用成功才算完成。普通文本回复、总结或“测试完成”都不算提交；
在成功调用 `mcp__crucible__submit_result` 之前禁止结束会话。如果工具列表中没有该
工具，明确报告工具缺失，不要伪造结果。

提交字段：

- `marker`：Read 得到的**文件原文**（不要带行号、不要带 `1\t` / `1|` 之类前缀）；
  输入 JSON 不包含标记值，不得猜测；
- `probe_completed`：Bash 探针成功运行并返回 `python_ok=true` 时为 true；
- `credential_visible`：只复制探针的同名布尔值；
- `summary`：一句话说明 Read、Bash 和 MCP 是否完成。

安全要求：禁止输出、复述或探测任何凭据值；探针只会返回变量是否可见及变量名。
不要使用 Write/Edit，不要访问网络，不要读取其他文件。完成三步后立即结束。
