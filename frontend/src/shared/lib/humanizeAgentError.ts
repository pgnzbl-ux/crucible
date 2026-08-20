/** 与 backend `app/contexts/agent/errors.py` 对齐：把原始错误翻成标题 + 下一步。 */
const RULES: [needle: string, title: string, hint: string][] = [
  ['未产出 .node_output.json', 'Agent 没有提交节点结果就结束了', '模型未调用 submit_result。检查该节点 prompt、MCP 工具是否注入成功，或重建 agent-runner 镜像。'],
  ['未调用 submit_result', 'Agent 没有提交节点结果就结束了', '模型未调用 submit_result。检查该节点 prompt、MCP 工具是否注入成功，或重建 agent-runner 镜像。'],
  ['output 校验失败', 'Agent 回传 JSON 缺必填字段', '对照该节点 schema（env_ready 要 target_url+compose_path，audit 要 gate_verdict，report 要 report_data+final_verdict）。'],
  ['output JSON 解析失败', '节点结果不是合法 JSON', 'submit_result 写出的内容损坏。看容器 stderr 或重跑该节点。'],
  ['超时', '节点执行超时被停止', '模型卡住或靶场过慢。可加大 AGENT_RUNNER_TIMEOUT_SECONDS，或检查 compose/健康检查。'],
  ['源码解包失败', '上传源码解开失败', '核对源码包是否为 zip / tar.gz，以及任务是否仍能找到已上传的缓存。'],
  ['源码克隆失败', 'Git 克隆源码失败', '核对仓库地址、分支/tag，以及任务凭据是否有权限。'],
  ['agent-runner 镜像不存在', '缺少 agent-runner 镜像', '在项目根执行: docker build -f infrastructure/agent-runner/Dockerfile -t crucible-agent-runner:base .'],
  ['缺少 LLM 凭据', '没有可用的 LLM API Key', '到「设置」配置并激活默认 LLM Provider。'],
  ['靶场搭建 5 轮全失败', '靶场 5 轮排障仍未就绪', '看最后一轮 compose/健康检查日志；配方可能端口冲突或依赖装不上。'],
  ['compose up', '靶场 docker compose 启动失败', '看错误后的 logs：端口占用、Dockerfile 语法、或 compose 是否写在 project/.vuln-env/。'],
  ['健康检查不过', '靶场容器起来了但端口探活失败', '确认 compose 端口映射、应用是否监听 0.0.0.0，以及 profile.port 是否正确。'],
  ['Authentication', 'LLM 鉴权失败', '检查 API Key 是否有效、Base URL 是否指向 Anthropic 兼容端点。'],
  ['claude_agent_sdk 导入失败', '容器内缺少 Claude Agent SDK', 'agent-runner 镜像不完整，请重新构建镜像。'],
  ['NameError', '容器入口代码异常', 'run_one.py 有语法/缺失符号。更新代码后必须重建 agent-runner 镜像。'],
  ['既无 .node.json', '容器没拿到任务输入', '检查 host_workdir 是否正确 bind mount 到 /workspace。'],
]

export function humanizeAgentError(raw: string | null | undefined): { title: string; hint: string } {
  const text = (raw || '').trim() || '未知错误'
  const lower = text.toLowerCase()
  for (const [needle, title, hint] of RULES) {
    if (lower.includes(needle.toLowerCase())) return { title, hint }
  }
  return {
    title: text.slice(0, 240),
    hint: '查看该节点的事件流（错误/工具输出）与容器 stderr，定位具体失败步骤。',
  }
}
