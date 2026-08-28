# FastAPI 猎洞提示

- `Depends(get_current_user)` 只证明"已登录"——路由函数里没有 ownership 比对（`db.get(Item, item_id)` 后不核对 `item.owner_id == current_user.id`）即对象级越权嫌疑。
- `router = APIRouter(dependencies=[...])` 只覆盖该前缀下声明时已挂的路由；逐路由核对依赖链。
- `background_tasks` / 直接返回 ORM 对象时注意响应模型是否漏 `response_model` 过滤（泄露密码哈希等字段也是候选：CWE-200）。
