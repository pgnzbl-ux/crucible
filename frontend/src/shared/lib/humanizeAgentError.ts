/** 与 backend `app/contexts/agent/errors.py` 对齐：标题 + 可操作下一步。 */
const RULES: [needle: string, title: string, hint: string][] = [
  ['余额不足', 'LLM 账户余额不足', '到服务商控制台充值，或更换可用的 API Key，再在「设置 → LLM Provider」更新后重试。'],
  ['"code":"1004"', 'LLM 账户余额不足', '到服务商控制台充值，或在「设置 → LLM Provider」更换可用的 API Key。'],
  ['LLM 调用失败', 'LLM 调用失败', '核对「设置 → LLM Provider」的 API Key、Base URL、模型名与账户余额。'],
  ['error result: success', 'LLM 会话异常结束', '多为上游接口报错（余额不足、模型不可用等）。请查看本节点更早的失败事件，并核对 Provider 配置。'],
  ['HTTP 401', 'LLM 接口鉴权失败（401）', '检查 API Key、Base URL 与账户状态；部分网关在余额不足时也会返回 401。'],
  ['未产出 .node_output.json', 'Agent 没有提交节点结果', '本轮分析未完成结构化回传。可从本节点重试；若反复出现，请检查 Provider 是否通过 Agent 测试。'],
  ['未调用 submit_result', 'Agent 没有提交节点结果', '本轮分析未完成结构化回传。可从本节点重试；若反复出现，请检查 Provider 是否通过 Agent 测试。'],
  ['output 校验失败', 'Agent 回传结果不完整', '缺少必填字段。请从本节点重试；持续失败时核对模型是否支持工具调用。'],
  ['output JSON 解析失败', '节点结果格式异常', '回传内容无法解析。请从本节点重试。'],
  ['超时', '节点曾超时结束', '可从本节点重试。若反复超时，请检查模型响应速度与 Provider 超时设置。'],
  ['源码解包失败', '上传源码解开失败', '确认源码包为 zip / tar.gz，且任务仍能访问已上传的文件。'],
  ['源码工作区准备失败', '源码工作目录权限异常', '目录可能被靶场进程改过属主。请先停止占用该任务目录的容器，再从「源码获取」节点重试。'],
  ['already exists and is not an empty directory', '源码工作目录没有清空', '工作区残留导致无法重新拉取。请停止相关容器后，从「源码获取」节点重试。'],
  ['源码克隆失败', 'Git 克隆源码失败', '核对仓库地址、分支/标签，以及任务凭据是否有访问权限。'],
  ['agent-runner 镜像不存在', '缺少 agent-runner 镜像', '请由管理员构建并加载 crucible-agent-runner:base 镜像后重试。'],
  ['缺少 LLM 凭据', '没有可用的 LLM API Key', '到「设置」配置并设为默认 LLM Provider。'],
  ['靶场搭建 5 轮全失败', '靶场 5 轮排障仍未就绪', '查看最后一轮启动与健康检查日志；常见原因是端口冲突或依赖安装失败。'],
  ['compose up', '靶场启动失败', '查看启动日志：端口占用、Dockerfile 语法，或配方是否放在仓库 .vuln-env 目录。'],
  ['健康检查不过', '靶场已启动但探活失败', '确认端口映射正确，应用监听 0.0.0.0，且画像中的端口配置无误。'],
  ['Authentication', 'LLM 鉴权失败', '检查 API Key 是否有效，Base URL 是否为 Anthropic 兼容端点。'],
  ['claude_agent_sdk 导入失败', 'Agent 运行环境不完整', '请重建 Agent 运行镜像后重试。'],
  ['NameError', 'Agent 运行入口异常', '请更新代码并重建 Agent 运行镜像后重试。'],
  ['既无 .node.json', '容器未收到任务输入', '请从本节点重试；持续失败时检查任务工作目录挂载是否正常。'],
]

export function humanizeAgentError(raw: string | null | undefined): { title: string; hint: string } {
  const text = (raw || '').trim() || '未知错误'
  const lower = text.toLowerCase()
  for (const [needle, title, hint] of RULES) {
    if (lower.includes(needle.toLowerCase())) return { title, hint }
  }
  return {
    title: text.slice(0, 240),
    hint: '查看本节点事件流中的错误与工具输出，定位失败步骤后重试。',
  }
}
