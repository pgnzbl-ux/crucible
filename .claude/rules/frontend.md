---
paths: ["frontend/src/**/*.ts", "frontend/src/**/*.tsx"]
---

# Crucible 前端规范

> 主线路线见 `docs/development-guide.md` §4。P1-5（按领域拆 store）仍开放，**不要为对齐本文件去先搬 Zustand**。

## 1. 目录分层

```
frontend/src/
├── app/            # providers.tsx（Query + antd）+ layout.tsx（壳：侧栏/顶栏）
├── pages/          # 路由页：组合 + 当前多数页面直接 useQuery(api.*)
├── features/       # 领域 UI（task / lab / dashboard）；尚无统一 store.ts
├── shared/
│   ├── components/ # 跨页组件（NodeSteps / ReportContent / MarkdownBody）
│   ├── hooks/      # useTaskEvents（SSE）、useStickToBottom
│   └── lib/        # api.ts（手写类型）+ meta.ts + 纯函数
└── styles/         # design-tokens.css + global.css + theme.ts
```

- `pages/` 应当薄；新代码优先把请求放进 `features/<domain>/`
- `shared/` **禁止** import `features/`
- 已登录路由：`App.tsx` 里 `AppLayout` 包住内容区 `Suspense`，切页不卸侧栏

## 2. 状态管理

- **服务端状态**：TanStack Query（`staleTime: 30s`；`retry` 跳过普通 4xx）
- **不要**再引入 Zustand，除非出现真正的跨树客户端状态（目前依赖里的 zustand 未使用）
- 派生 UI 用 `useState`
- SSE 生命周期只在 `useTaskEvents`，组件禁止 `new EventSource()`

## 3. API 客户端（`shared/lib/api.ts`）

- 类型手写，OpenAPI 生成是 P2-12
- 失败抛 `ApiError(message, status, code?)`；登录/注册/setup 用 `skipAuth`，401 展示信封，不当成会话过期
- 带 `Authorization` 的 401 才 `handleUnauthorized`（清 token + 跳 `/login`）
- 不硬编码 `owner_id`

## 4. SSE

- `GET /api/v1/tasks/{id}/events/stream`；token 只能走 query（EventSource 无自定义 header）
- 401 时先 `GET /auth/me`，过期则停重连
- 任务详情独立路由 `TaskDetailPage` + `TaskDetailTabs`，**不是**列表 Drawer

## 5. 表单

- Ant Design 6 + `Form.useForm()` / `Form.Item`
- 客户端校验只作 UX；敏感字段 `Input.Password`，回显掩码

## 6. 路由与权限

- 守卫在 `App.tsx` 的 `RequireAuth`（看 localStorage token 是否存在）
- 无 RBAC / 403 页；后端是权限真相源
- 详情页 `fill`：`/tasks/:id`、`/reports/:id`

## 7. 样式

- 主题 token：`styles/theme.ts` + `design-tokens.css`
- 业务组件用 CSS 变量 / antd Token，不写死 `#1677ff`
- 无 `@emotion/css`；图标用 `@ant-design/icons`

## 8. 反馈

- 操作结果、登录/注册失败、列表或查询失败：`App.useApp().message` toast
- `Alert` 只留给页面里需要持续阅读的内容（任务失败原因、报告说明、事件卡片），不要用 Alert 当通知条
