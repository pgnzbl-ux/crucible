# Crucible 前端

React 控制台。产品说明与全仓启动见仓库根目录 [README.md](../README.md)。后端见 [../backend/README.md](../backend/README.md)。

## 职责

登录后的工作台：任务、源码、靶场、报告、设置。开发服务器把 `/api` 代理到后端 `http://localhost:8010`。

## 启动

后端 API（8010）需要先起来，否则页面调接口会失败。

```bash
cd frontend
npm install
npm run dev
```

打开 [http://localhost:5173](http://localhost:5173)。库中还没有账号时，登录页会创建第一个账号。

## 页面

| 路径 | 内容 |
| --- | --- |
| `/` | 工作台 |
| `/tasks`、`/tasks/:id` | 任务列表与详情（节点进度、Agent 过程） |
| `/projects`、`/projects/:id` | 源码项目 |
| `/labs` | 靶场 |
| `/reports`、`/reports/:id` | 验证报告 |
| `/settings` | LLM Provider 与凭据 |
| `/login` | 登录 |

## 目录

```
frontend/src/
├── app/          # 布局、鉴权、Query / 主题
├── pages/        # 路由页面
├── features/     # 任务、靶场、工作台领域组合
└── shared/       # API 客户端、SSE、通用组件
```

本包是 private 应用，不发布 npm，`package.json` 不写 `version`。产品版本以后端 `pyproject.toml` 为准。

## 脚本

```bash
npm run dev          # 开发服务器，端口 5173
npm run typecheck    # tsc --noEmit
npm test             # vitest
npm run build        # 生产构建
```
