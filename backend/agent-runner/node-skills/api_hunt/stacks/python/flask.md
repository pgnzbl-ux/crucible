# Flask 猎洞提示

- `@login_required` / `before_request` 只证明登录；blueprint 级 `before_request` 覆盖面要逐 blueprint 核对。
- `g.user` 与资源属主比对缺失：`Model.query.get_or_404(id)` 直接返回即嫌疑（对象级越权）。
- `request.args.get('id')` 进 `filter(...)` 的原始字符串拼接（`text(...)` / `execute(f"...")`）同时标 SQL 注入候选。
