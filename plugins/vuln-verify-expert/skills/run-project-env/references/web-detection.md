# Web / Web API 类型判定

第 2 步的门禁规则。本 skill **只处理 web 与 web api 项目**，其它类型建立全景后即终止，避免为不需要环境的项目白做工。

## 判定为 web / web api（继续处理）

命中任一即可认定：

### 后端 HTTP 服务框架

| 语言 | 关键依赖/特征 |
|------|--------------|
| Java | `spring-boot-starter-web`、`spring-webmvc`、`javax.servlet`/`jakarta.servlet`、Tomcat/Jetty/Undertow、`@RestController`/`@Controller` |
| Node.js | `express`、`koa`、`@nestjs/core`、`fastify`、`next`、`nuxt`、`hapi` |
| Python | `flask`、`fastapi`、`django`、`starlette`、`uvicorn`/`gunicorn`、`tornado` |
| Go | `net/http`、`gin`、`echo`、`fiber`、`chi`、`gorilla/mux` |
| PHP | `laravel`、`symfony`、`slim`、根目录 `index.php` + `public/` |
| Ruby | `rails`、`sinatra`、`rack` |
| Rust | `actix-web`、`axum`、`rocket`、`warp` |
| .NET | `Microsoft.AspNetCore.*`、`Startup.cs`/`Program.cs` with `WebApplication` |

### 其它信号

- 配置中有 `server.port` / `PORT` / `listen(<port>)` 且提供 HTTP 服务。
- 有 REST/GraphQL 路由定义、controller 目录、`routes/` 目录。
- README 里出现"访问 http://..."、"接口文档"、"Swagger"、"API"、"管理后台"等。
- 有前端构建产物 + 后端 API（全栈项目）。
- 提供 WebSocket 服务也算 web。

**纯前端静态站点**（只有 HTML/CSS/JS 或 SPA 构建产物，无后端）也属于 web，用 nginx 类镜像托管即可。

## 判定为非 web（建立全景后终止）

以下类型明确告知用户"这是 <类型> 项目，不是 web/web api，本 skill 不处理"，不再往下走：

- **CLI 工具 / 命令行程序**：入口是命令行参数解析，无监听端口。
- **库 / SDK / 框架**：供他人依赖，无独立运行入口（`packaging=jar` 但无 main web、npm library 无 start server）。
- **桌面应用**：Electron 之外的 GUI（如 JavaFX、Qt、WPF、Tauri 桌面壳）。
- **数据处理 / 批处理 / ETL / 训练脚本**：跑完即退出，无常驻服务。
- **纯脚本集合 / 配置仓库 / 文档站源码**（文档站若需构建成可访问站点则算 web，按前端静态站处理）。
- **移动 App**（Android/iOS 原生）。

## 边界情况

- **微服务单模块**：只要该模块本身提供 HTTP 服务即算 web，正常处理。
- **既是库又带 demo server**：以能否起 HTTP 服务为准，能起就处理 demo server。
- **CLI + 内嵌 web dashboard**：算 web，处理其 web 部分。
- **拿不准时**：倾向于查看是否有监听端口的常驻进程。有则 web，没有则非 web。可简要向用户说明判断依据。
