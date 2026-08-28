# 常见场景实战专篇 (Framework Cookbooks)

场景打法与第一性原理。**通用语言模板的唯一来源是 dockerfile-patterns.md**，本篇不存重复副本，只保留场景差异化增量；需要完整 Dockerfile 时先取对应模板，再叠加本篇场景要点。

---

## 目录
1. [沙箱容器化铁律与特权系统降级取舍](#一沙箱容器化铁律与特权系统降级取舍)
2. [Java 场景：JDK 矩阵 / 微服务编排（模板见 patterns 模板 1-4）](#二java-场景)
3. [PHP 场景：安装向导与 5.6 遗留（模板见 patterns 模板 1-3）](#三php-场景)
4. [Python 场景：Django 迁移 / FastAPI / Flask / Celery 降级](#四python-场景)
5. [Node.js 场景：Next standalone / NestJS / Monorepo](#五nodejs-场景)
6. [Go / Rust / .NET / Ruby / 静态 SPA：零增量，直接用 patterns 模板](#六go--rust--net--ruby--静态-spa)
7. [中间件与数据库初始化全量速查表（全平台唯一权威表）](#七中间件与数据库初始化全量速查表)

---

## 一、沙箱容器化铁律与特权系统降级取舍

### 1. 核心铁律
- **所有靶场必须在普通无特权 Docker 容器中拉起**。
- **严禁依赖宿主机特权**：不得使用 `privileged: true`、不得挂载宿主机 `/var/run/docker.sock`、不得使用 `network_mode: host`。

### 2. 特权项目降级与 Mock 取舍策略
当目标项目本身是一个**依赖宿主 Docker 守护进程或特权内核**的系统（如 CI/CD 平台、容器管理面板、代码沙箱平台、安全扫描调度引擎）时：

```
[目标项目 Web 控制台 / 核心 API] ──(必须保留)──> 供 Agent 进行 HTTP 漏洞复现与 PoC 验证
          │
          └── [后台特权 Worker / Docker 调度引擎] ──(主动 Mock / 禁用 / 裁剪)
```

- **配置裁剪**：在 `.env` 或 `application.yml` 中关闭 Docker 特权功能，例如：
  - `DOCKER_ENABLED=false` 或 `RUNNER_TYPE=mock` / `EXECUTION_ENGINE=local`
  - `CELERY_TASK_ALWAYS_EAGER=true` / `WORKER_ENABLED=false`
- **只起 Web 主干**：若项目同时包含 `api-server` 和 `docker-worker`，在 compose 中**只编排 Web/API 服务与数据库**，注释掉无法在容器内运行的 Docker-in-Docker worker。
- **目标优先级**：**“Web 路由通畅、登录可用、漏洞点接口可达” 远重于 “全套复杂的底层执行引擎 100% 运转”**。

---

## 二、Java 场景

### 1. 架构形态与 JDK 矩阵速查
| 形态 | 识别特征 | 运行方式 | 适用 JDK |
|---|---|---|---|
| **传统 WAR 包** | `<packaging>war</packaging>` 或含 `WEB-INF/web.xml` | Tomcat 容器（`catalina.sh run`），产物放 `webapps/ROOT.war` → **patterns Java 模板 2** | 老项目通常 **JDK 8**；JDK 11 部分；JDK 17+ 极少 |
| **Spring Boot Executable JAR** | `spring-boot-starter` in pom/gradle | `java -jar` → **patterns Java 模板 1** | JDK 8 / 11 / 17 / 21 均常见，看 `<java.version>` |
| **多模块工程** | 根 pom 含 `<modules>` | 根目录全量构建取主模块产物 → **patterns Java 模板 3** | 依 pom 声明 |
| **Gradle 工程** | `build.gradle` / `build.gradle.kts` | **patterns Java 模板 4**；必带 `--no-daemon -x test` | 依 `sourceCompatibility` |
| **Spring Cloud 微服务** | Eureka / Nacos / Gateway / Auth / Business | 见下方编排要点 | JDK 8/11/17 |

### 2. Spring Cloud 微服务编排要点
- Gateway、Auth、核心业务模块各自成 compose service，**一容器一进程**；注册中心（Eureka/Nacos）与中间件（Redis/MySQL）按指纹补为独立服务（镜像见 §七）。
- 所有注册中心 / DB 地址用环境变量覆盖为 **compose 服务名**；不要为了保留 `localhost` 把多个 JVM 塞进一个容器。
- 启动顺序：注册中心 → 配置中心 → 业务服务，用 `depends_on` + healthcheck 控序；业务服务给足 `start_period`。

---

## 三、PHP 场景

### 1. 传统 CMS 与 Web 安装向导 第一性原理
1. **安装向导机制**：许多系统通过前端向导（`/install.php`、`/install/index.php`）初始化数据库与管理员账号。探活命中安装向导页面（200/302）即判定成功，无需强行拼凑私有库。
2. **权限要求**：运行时必须具备写权限，否则向导会阻塞在“目录不可写”→ `chmod -R 777`。
3. **扩展支持**：必须包含 `pdo_mysql`, `mysqli`, `gd`, `zip`, `mbstring`, `bcmath`, `curl`, `xml`, `opcache`。
4. **Nginx FastCGI PATH_INFO 与超时**：确保 FastCGI 正确解析子路由，并设置 `fastcgi_read_timeout 300;` 避免初次建表导入超时断开（504）。
5. **PHP 5.6 遗留系统（mysql_* 废弃函数）**：若源码含 `mysql_connect()`（非 `mysqli_connect`）或使用 `<?` 短标签，**必须选用 `php:5.6-fpm-alpine`** 并启用 `short_open_tag = On`。

### 2. 模板选择
- Discuz! / 禅道 / WordPress / DedeCMS / Typecho 等安装向导类 → **dockerfile-patterns.md PHP 模板 1**（PHP 7.4/8.1 + 全扩展 + 777）。
- 老旧 CMS（`mysql_*` / 短标签）→ **PHP 模板 3**（PHP 5.6 专版）。
- ThinkPHP / Laravel / Symfony / Yii2 等现代框架（文档根 `/public`）→ **PHP 模板 2**。

---

## 四、Python 场景

基础镜像与多阶段骨架用 **dockerfile-patterns.md Python 模板**，以下只换 CMD / 环境变量。

### 1. Django（先迁移再起服务 + 放开 Host 校验）
`DJANGO_SETTINGS_MODULE` 按项目实际 settings 路径调整（`ENV DJANGO_SETTINGS_MODULE=project.settings`）：

```dockerfile
# 启动脚本：先跑 migrate，再启动 Gunicorn
CMD ["sh", "-c", "python manage.py migrate --noinput && gunicorn --bind 0.0.0.0:8000 --workers 2 project.wsgi:application"]
```
配合 `ALLOWED_HOSTS = ['*']` 环境变量或启动参数，否则平台探活被 `DisallowedHost` 拦截。

### 2. FastAPI / Flask
```dockerfile
# FastAPI
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
# Flask
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "app:app"]
```

### 3. Celery Worker
后台任务依赖 Celery 时，靶场内不起独立 Worker，用 `CELERY_TASK_ALWAYS_EAGER=true` 让任务进程内同步执行（见 §一.2）。

---

## 五、Node.js 场景

基础构建/运行骨架用 **dockerfile-patterns.md Node.js 模板**（含 `openssl-legacy-provider` 处理）。

### 1. Next.js standalone 产物拷贝（替换 runtime 段的 COPY 逻辑）
```dockerfile
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public
CMD ["node", "server.js"]
```

### 2. NestJS / Express / Nuxt（编译 + ORM 迁移）
```dockerfile
# 编译阶段
RUN npm run build
# 运行阶段：Prisma 项目先推平表结构
CMD ["sh", "-c", "npx prisma db push --skip-generate || true && node dist/main.js"]
```

### 3. Monorepo 工程 (pnpm / yarn workspaces)
```dockerfile
# 在 root 下拉依赖，只 build 目标 package
FROM node:20-slim AS builder
WORKDIR /app
RUN corepack enable
COPY pnpm-lock.yaml pnpm-workspace.yaml package.json ./
COPY packages/ ./packages/
COPY apps/ ./apps/
RUN pnpm install --frozen-lockfile
RUN pnpm --filter <target-app> build
```

---

## 六、Go / Rust / .NET / Ruby / 静态 SPA

零场景增量，直接取 **dockerfile-patterns.md** 对应模板：

| 场景 | 模板 |
|---|---|
| Go (Gin / Fiber / Echo / 标准库) | Go 模板（已含静态资源拷贝） |
| Rust (Actix-web / Axum / Rocket) | Rust 模板（已含依赖预热与 tini） |
| ASP.NET Core 6/8 | .NET 模板（`ASPNETCORE_URLS=http://+:8080`） |
| Ruby on Rails | Ruby on Rails 模板（启动即迁移）；Sinatra 端口 4567/8080 |
| Vue / React / Vite SPA | 静态站点模板（SPA 路由 `try_files ... /index.html`） |

---

## 七、中间件与数据库初始化全量速查表

本表是**全平台唯一权威表**（镜像 tag 以此为准，不再维护第二份）。

| 中间件 | 推荐镜像 | 关键参数 / 环境变量 | 数据库初始化挂载路径 |
|---|---|---|---|
| **MySQL 5.7** (老旧项目首选) | `mysql:5.7` | `command: --character-set-server=utf8mb4 --collation-server=utf8mb4_unicode_ci --max_allowed_packet=64M` | `/docker-entrypoint-initdb.d/*.sql` |
| **MySQL 8.0** (现代项目) | `mysql:8.0` | `command: --default-authentication-plugin=mysql_native_password --max_allowed_packet=64M` (解决兼容认证与大包截断) | `/docker-entrypoint-initdb.d/*.sql` |
| **PostgreSQL** | `postgres:16-alpine` | `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | `/docker-entrypoint-initdb.d/*.sql` |
| **Redis** | `redis:7-alpine` | `healthcheck: test: ["CMD", "redis-cli", "ping"]` | 内存无初始表 |
| **MongoDB** | `mongo:7` | `MONGO_INITDB_ROOT_USERNAME`, `MONGO_INITDB_ROOT_PASSWORD` | `/docker-entrypoint-initdb.d/*.js` |
| **RabbitMQ** | `rabbitmq:3-management-alpine` | 端口 `5672` (AMQP) 与 `15672` (Web 管理台) | 健康检查 `rabbitmq-diagnostics -q ping` |
| **Elasticsearch 8.x** | `elasticsearch:8.11.0`（tag 必须具体，无 `8.x` 这种写法） | `environment: discovery.type=single-node`、`xpack.security.enabled=false`、`ES_JAVA_OPTS=-Xms512m -Xmx512m`（单节点免 bootstrap 检查、关 TLS 认证、限堆防 OOM） | 无（索引由应用或脚本建） |
| **MinIO (S3)** | `minio/minio`（锁明确 RELEASE tag，避免 `latest`） | `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`, `command: server /data --console-address ":9001"` | 建桶用 `minio/mc` 一次性 init 容器或应用自带建桶逻辑 |
| **Nginx** | `nginx:alpine` | 反代/静态托管；PHP 场景见 patterns PHP 模板 | 无 |

注意：以上环境变量全部**写死字面量**进 compose——compose 文本里 `${VAR}`/`$VAR` 会被平台策略直接拒绝（见 startup-troubleshooting.md 禁止清单）。
