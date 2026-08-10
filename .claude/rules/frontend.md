---
paths: ["frontend/src/**/*.ts", "frontend/src/**/*.tsx"]
---

# Crucible 前端规范

> 主线路线（特别是 P0-1 SSE / P0-3 JWT 联调 / P1-5 features 填充）见 `docs/development-guide.md` §4。

## 1. 目录分层

```
frontend/src/
├── app/            # providers.tsx + layout.tsx（AppLayout + 侧边栏）
├── pages/          # 路由级页面（Dashboard / Tasks / Reports / Settings / Login）
├── features/       # ★ 领域模块（task / agent / auth / report），每个含 store.ts + hooks.ts
├── shared/
│   ├── components/ # 跨领域复用组件
│   ├── hooks/      # 通用 hooks（useSSE / usePolling / etc.）
│   ├── lib/        # api.ts（类型化 API 客户端）+ meta.ts（枚举映射）
│   └── types/      # 跨领域共享类型
└── styles/
```

- 业务逻辑只放在 `features/<domain>/`，不在 `pages/` 中实现
- 页面是**组合**层：从 features 拉 store + hooks + 组件，不直接发请求
- `shared/` 不允许 import `features/`（依赖方向只能向内）

## 2. 状态管理

- **服务端状态**：TanStack Query（缓存、失效、重试）
- **客户端状态**：Zustand store，**每个领域一个 store**（不要堆成一个大 store）
- **派生 UI 状态**：`useState` / `useReducer`，不要进全局 store
- 事件流（SSE）只放进对应领域 store，不在组件里维护 SSE 生命周期

## 3. API 客户端（`shared/lib/api.ts`）

- 类型**自动从后端 OpenAPI 生成**（P2-12 待办）；过渡期手动维护但要标 `@generated-from` 注释
- 错误响应统一处理：抛 `ApiError(status, code, message)`，业务层 catch 后显示
- token 从 `localStorage` 读取，401 自动跳登录页（路由守卫）
- `owner_id` 从 token 解析，不硬编码 `"system"`

## 4. SSE 实时事件（P0-1 已实现）

- `shared/hooks/useTaskEvents.ts` 已封装 EventSource + 指数退避重连 + 卸载清理
- 频道：`GET /api/v1/tasks/{id}/events/stream`，订阅后端 Redis Pub/Sub 转发；启动先回放历史
- 组件用 `useTaskEvents(taskId)` 拿事件流，**不要**在组件里 `new EventSource()`
- token 走 query `?token=<jwt>`（EventSource 不支持自定义 header），P0-3 接入鉴权后统一收口
- TasksPage 详情 Drawer 已切换：SSE 实时事件流 + 连接状态指示 + agent.completed/failed 触发 task/report 刷新

## 5. 表单与受控组件

- 表单统一用 Ant Design 5 + `Form.useForm()`
- 提交前客户端校验**只作 UX**，后端校验仍是**真相**（参考 `error-handling.md` 与 `validation.md`）
- 敏感字段（API Key、密码）用 `Input.Password`，回显时只显示掩码（`***last4`）

## 6. 路由与权限

- 路由守卫放在 `app/layout.tsx`：未登录 → `/login`；无权限 → 403
- 当前 JWT 单一鉴权，OIDC 增量叠加时改此守卫即可（不破坏已有逻辑）
- 不在前端做角色判断后再隐藏后端调用——后端是**真相源**，前端隐藏只是 UX

## 7. 样式

- Ant Design 5 主题变量集中在 `styles/theme.ts`，业务组件不直接写 `#1677ff` 等硬编码色值
- CSS-in-JS 用 `@emotion/css`（项目已依赖），避免散落的 styled-components
- 图标统一用 `@ant-design/icons`