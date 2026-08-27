# Laravel 猎洞提示

- 先读 `route_file` 确认路由与中间件（`auth` / `can` / 自定义），再打开 `handler_file` 看控制器方法。
- 重点查：路由 `{id}` / `$request->input()` / `Route::` 绑定是否在读写前做 `authorize` / Policy / `$user->id` 比对。
- `FormRequest` 校验字段存在 ≠ ownership；缺 Policy 且按 id 取模即嫌疑。
