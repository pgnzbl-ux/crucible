# Spring 猎洞提示

- 鉴权入口三处逐一核对：`SecurityFilterChain` 的 `requestMatchers`（permutations/`hasRole`）、控制器方法上的 `@PreAuthorize`/`@PostAuthorize`、HandlerInterceptor。三者都缺席 = 该路由仅靠登录态。
- 对象级越权：`@PathVariable id` / `@RequestParam` 直接 `repository.findById(id)` / `mapper.selectById(id)` 即嫌疑——确认是否有 `ownership` 比对（`eq(userId)`）或 `@PostAuthorize("returnObject.user == principal")`。
- MyBatis：XML / 注解里 `${}` 是拼接（找 `${orderBy}`、`${tableName}`）；`#{}` 安全。`@Select` 动态排序参数重点看。
- 管理面：`management.endpoints.web.exposure.include` 若含 `env/heapdump` 且无独立端口/鉴权，直接候选。
