# Next.js 猎洞提示

- App Router：`app/api/**/route.ts` 每个导出函数独立鉴权——`middleware.ts` 的 `matcher` 有漏路径时，route handler 内必须自查 `getServerSession()`/`getToken()`；两者都不在 = 候选。
- `params: { id }` 动态段直接 `prisma.<model>.findUnique({ where: { id } })` 而无 `userId` 过滤即对象级越权嫌疑。
- `middleware.ts` 里的路径排除（`public`/`_next`/白名单数组）逐项核对是否把业务 API 也排除了。
