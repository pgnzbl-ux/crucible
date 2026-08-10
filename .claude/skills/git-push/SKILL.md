---
name: git-push
description: 安全地把本地变更推送到 origin
---

# Git Push

## 步骤

1. **提交前检查**（参考 `git-workflow.md` §3）

   ```bash
   git status
   git grep -nE "sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{30,}"   # 必须零命中
   cd backend && ruff check app tests   # 待 P2-14 接入
   cd frontend && npx tsc --noEmit
   ```

2. **写 commit**（英文，参考 `git-workflow.md` §2）

   ```bash
   git add -A
   git commit -m "feat(agent): add SSE event publishing

   Publish Agent lifecycle events to Redis Pub/Sub for P0-1 SSE
   endpoint.

   Refs: P0-1"
   ```

3. **推分支**

   ```bash
   git push -u origin feat/sse-events
   ```

4. **开 PR**（如 CLI 已配）

   ```bash
   gh pr create --base main --title "feat(agent): SSE events" --body "..."
   ```

## 安全红线

- 严禁 `--force` 推 main
- 严禁跳过 hook（`--no-verify`）除非用户明确同意
- 推送前再 grep 一遍敏感信息