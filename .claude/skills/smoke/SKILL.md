---
name: smoke
description: 全链路端到端冒烟（创建任务 → 沙箱 → Agent → 报告）
---

# Smoke (E2E)

任意 P0 改动后必须跑一次。

## 步骤

1. **环境就绪**：deploy skill 跑通，`/health` 200
3. **注册 + 登录**

   ```bash
   TOKEN=$(curl -sX POST http://localhost:8010/api/v1/auth/register \
     -H 'Content-Type: application/json' \
     -d '{"username":"smoke","password":"Smoke123!"}' | jq -r .id)  # 仅取 ID；下面有登录
   ```

   ```bash
   TOKEN=$(curl -sX POST http://localhost:8010/api/v1/auth/login \
     -H 'Content-Type: application/json' \
     -d '{"username":"smoke","password":"Smoke123!"}' | jq -r .access_token)
   ```

4. **创建任务**（`CLAUDE_AGENT_SDK_ENABLED=false` 时仍走 6 节点编排，不拉真实 LLM）

   ```bash
   TASK_ID=$(curl -sX POST http://localhost:8010/api/v1/tasks \
     -H "Authorization: Bearer $TOKEN" \
     -H 'Content-Type: application/json' \
     -d '{"repo_url":"https://github.com/octocat/Hello-World","description":"smoke test","priority":"P2"}' \
     | jq -r .id)
   ```

5. **轮询直到完成**（timeout 60s）

   ```bash
   for i in {1..30}; do
     curl -s "http://localhost:8010/api/v1/tasks/$TASK_ID" \
       -H "Authorization: Bearer $TOKEN" | jq .status
     sleep 2
   done
   ```

6. **看事件流**

   ```bash
   curl -s "http://localhost:8010/api/v1/tasks/$TASK_ID/events" \
     -H "Authorization: Bearer $TOKEN" | jq '.items[] | {type, ts: .timestamp}'
   ```

7. **确认报告**

   ```bash
   REPORT_ID=$(curl -s "http://localhost:8010/api/v1/tasks/$TASK_ID" \
     -H "Authorization: Bearer $TOKEN" | jq -r .report_id)
   curl -s "http://localhost:8010/api/v1/reports/$REPORT_ID" \
     -H "Authorization: Bearer $TOKEN" | jq '{status, has_content: (.content != null)}'
   ```

## 通过标准

- 任务最终 status = `completed` 或 `failed`（不允许 `running` 滞留）
- 事件流非空
- 报告 status = `completed` 且有内容

## 失败处理

- 看 Celery worker 日志
- 看任务 events 找首个 error
- 看沙箱：`docker ps -a --filter "label=crucible"`