# Crucible Git 工作流

> 全局规则。所有项目成员（含 AI agent）提交前必须遵守。

## 1. 分支策略

- `main` —— 保护分支，必须 PR 合入
- `feat/<short-name>` —— 新功能，从 `main` 切
- `fix/<short-name>` —— bug 修复
- `chore/<short-name>` —— 重构 / 文档 / 杂项
- `<short-name>` 用 kebab-case，3-5 词

## 2. Commit Message（简体中文，覆盖全局英文默认）

> 项目根 CLAUDE.md 明确"本项目 commit message 用简体中文"。Conventional Commits 类型前缀(feat/fix 等)+ `Co-Authored-By` trailer 仍用英文(工具链解析要求);描述主体、bullet、footer 一律中文。

```
<type>(<scope>): <subject>

<body>

<footer>
```

- `type`: feat / fix / chore / refactor / docs / test / perf
- `scope`（可选）: backend / frontend / agent / sandbox / ci / ...
- `subject`: 祈使句小写、不超过 50 字符、不加句号
- `body`: 解释**为什么**（不是"做了什么"——diff 已经说明）
- `footer`: 关联 issue / breaking change

## 3. 提交前检查

- [ ] 无敏感信息（参考 `security.md` §1：`git grep -nE "sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{30,}"` 必须零命中）
- [ ] `cd backend && ruff check app tests` 通过（待 P2-14 接入）
- [ ] `cd frontend && npx tsc --noEmit` 通过
- [ ] 受影响模块的测试通过（参见 `testing.md` §1 层级）
- [ ] docs/development-guide.md「已完成清单」如有进展同步更新

## 4. PR

- 标题 = commit subject
- 描述必含：背景 / 改动 / 验证（截图 / 日志 / 命令输出）
- 关联 docs/development-guide.md 的 P0/P1/P2 编号（如"P0-1 SSE"）
- 单 PR 不超过 400 行 diff（除 P1 大特性外）

## 5. 不可逆操作

执行下列操作前必须用户确认（参考全局 CLAUDE.md）：

- `git reset --hard`
- `git push --force`
- 删除分支 / tag
- `docker compose down -v`（删除卷）
- 数据库 drop / truncate

## 6. 标签与发布

- 版本号遵循 SemVer：`vMAJOR.MINOR.PATCH`
- 标签由维护者手打，AI agent 不主动 tag
- Release notes 从 PR 自动生成（待 P2-14）