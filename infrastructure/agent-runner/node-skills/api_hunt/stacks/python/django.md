# Django / DRF 猎洞提示

- `IsAuthenticated` ≠ 对象级：`get_queryset()` 未按 `request.user` 过滤（直接 `get_object_or_404(Model, pk=pk)`）、且未实现 `has_object_permission` 时，任何登录用户可读改他人资源——重点核对 `permission_classes` 与 `get_queryset`。
- `@permission_classes` 方法级覆盖视图集默认值；逐个视图核对，别信类的默认。
- 原生 Django 视图：`@login_required` 只管登录；`Model.objects.get(pk=request.GET['id'])` 直接嫌疑。
