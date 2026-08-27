# Express 猎洞提示

- 中间件**注册顺序即生效范围**：`authMiddleware` 若挂在某路由之后（或只挂在一个 `app.use` 分支），其后的路由是裸奔的——逐条对照 `app.use`/`app.get` 声明行位置。
- 无框架级 RBAC 是常态：`req.params.id` / `req.body.userId` 直接 `Model.findByPk(id)` / `findById(id)` 而没有 `where: { userId: req.user.id }` 即嫌疑。
- `req.user` 从 JWT 解出后是否被后续 handler 信任为"资源属主"要核对取值来源（token 里取 ≠ 资源属主）。
