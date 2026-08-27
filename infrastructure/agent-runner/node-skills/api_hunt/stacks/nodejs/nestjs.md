# NestJS 猎洞提示

- Guard 是**逐控制器/逐方法**装饰的：`@UseGuards(JwtAuthGuard)` 缺席的 controller = 公开；`@UseGuards` 有但无 `@Roles()`/自定义 `RolesGuard` = 仅登录不鉴权。
- 对象级：`service.findOne(id)` / `repo.findOne({ where: { id } })` 没带 `userId`（或无 `@CurrentUser` 比对）即嫌疑。
- `@ParseIntPipe` 只管类型转换，与鉴权无关，不要当作防御层。
