# Gin 猎洞提示

- `r.Group("/api", authMiddleware())` 的分组覆盖要逐路由核对：散装的 `r.GET`（不带组）常漏掉中间件。
- `c.Param("id")` / `c.Query("id")` 直接 `db.First(&obj, id)` 而无 `user_id = ?` 条件即对象级越权嫌疑。
- JWT 中间件通常只填 `c.Set("userID", ...)`——handler 里是否真的取出来参与查询过滤才是判定点。
