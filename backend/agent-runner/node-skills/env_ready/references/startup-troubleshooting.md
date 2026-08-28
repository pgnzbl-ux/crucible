# 失败对症排障参考

平台闭环里你**没有 Docker CLI、也看不到完整日志**——排障输入是三样：`failed_stage`（哪一层失败）、`previous_error`（平台捕获的构建/启动/探活错误片段）、以及源码本身。本参考把"现象 → 根因层级 → 改哪里"固化成速查表；纪律仍是一次只改一处。

## 真实错误日志深度解构方法论 (Error-Driven Diagnostics)

静态知识库无法穷举所有报错。当 `attempt > 1` 时，**必须以 `previous_error` 中的真实堆栈与日志为第一输入**：

1. **查找最深层的 Root Cause 关键字**：
   - **Java**：向上查找最后一个 `Caused by: <ExceptionName>: <Message>`，通常指示最根因（如 `ClassNotFoundException`, `PropertyNotFoundException`, `SQLException`）。
   - **Python**：定位 Traceback 最底部的报错行（如 `ModuleNotFoundError: No module named 'xxx'` → Dockerfile 补装该库；`django.core.exceptions.DisallowedHost` → 设 `ALLOWED_HOSTS = ['*']`）。
   - **PHP**：查找 `Fatal error: Uncaught Error: Call to undefined function <func_name>()` → 补齐相应 PHP 扩展；查找 `Permission denied` → 补 `chmod -R 777`。
   - **Node.js**：查找 `Error: Cannot find module 'xxx'` → `npm install` 缺少依赖；`PrismaClientInitializationError` → 数据库未连接或表未迁移。
2. **解读 Docker 容器退出码 (Exit Codes)**：
   - `exit=137`：**OOM (内存溢出)**。修法：在 Dockerfile/Compose 限制 JVM 堆内存（如 `-Xmx512m`），或减少并发 Worker 数量。
   - `exit=127`：**命令不存在 (Command not found)**。修法：基础镜像缺少该工具（如 `sh`, `bash`, `tini`, `node`），在 Dockerfile 中通过包管理器安装。
   - `exit=1` / `exit=2`：进程启动即崩溃。检查容器启动命令（`CMD` / `ENTRYPOINT`）是否语法错误、前台运行参数是否缺失（如 nginx 需 `daemon off;`，Tomcat 需 `catalina.sh run`）。
3. **未枚举错误的通用泛化修复原则**：
   - 看到 `Connection refused: <host>:<port>` → 检查 Compose 是否有该服务名，是否缺少环境变量覆盖。
   - 看到 `Table 'xxx' doesn't exist` → 在源码目录搜索 `*.sql` 并在 Compose 中挂载至 `/docker-entrypoint-initdb.d/`。
   - 看到 `Access denied for user 'xxx'@'%'` → 检查数据库服务中的环境变量 `MYSQL_USER`/`MYSQL_PASSWORD` 与应用端配置是否完全一致。

## 根因层级 × failed_stage 对照

| failed_stage | 层级 | 典型现象 | 改哪里 |
|---|---|---|---|
| `compose_build` | 构建期 | 依赖装不上、编译错误、COPY 路径错 | Dockerfile / build.context |
| `compose_timeout` | 构建期 | compose up 超时 | 精简依赖与安装步骤、拆轻构建 |
| `container_start` | 编排期 | service 起不来、互相连不上、启动顺序错 | docker-compose.yml |
| `container_healthcheck` | 编排/应用期 | 容器起了但健康检查不过 | healthcheck 定义 / 应用配置 |
| `health_check` | 应用期 | HTTP 通了但正文崩溃（Fatal/缺表/Whitelabel/traceback） | 应用配置 / 迁移 / 环境变量 |
| `port_conflict` | 编排期 | 宿主端口被其他容器占用 | 只改 compose host 侧映射口 |
| `recipe_validation` | 校验期 | `initial_creds` 三态不合法；compose 没把 Web 端口映射到宿主 | 修 `initial_creds` 写法 / 给 Web 服务加 `ports` |
| `ai_submit` | 提交期 | 未调 `submit_result` 或形状非法 | 本轮务必提交合法配方 |
| `cached_recipe` | 校验期 | 历史缓存配方复验失败 | 当作全新任务完整重写 |
| `compose_policy` | 策略期 | 安全策略拒绝（见下方禁止清单） | 按清单逐项清除 |

## compose_policy 禁止清单（平台强校验，违者直接拒绝、白烧一轮）

平台在 `compose up` 前对 compose 文件做静态安全审查。写配方时一次到位，别等报错再改：

- **`${VAR}` / `$VAR` 环境变量插值**：全文任何 `$` + 字母/下划线都会被拒（nginx 的 `$uri`、shell 的 `$PWD` 同样命中）；确需字面 `$` 用 `$$` 转义。原因：compose 会从进程环境做插值，平台为防密钥经此泄入靶场直接封禁——所有值写死字面量。
- **`env_file`**：不允许引用外部 `.env`；需要的环境变量逐个写进 `environment`。
- **`secrets` / `configs` / `extends` / `include`**：顶层与服务级都禁止（防挂载宿主敏感文件）。
- **特权与命名空间**：`privileged`、`network_mode`/`pid`/`ipc: host`、`container:`/`service:` 跨容器共享、`cap_add`、`devices`、`security_opt`、`userns_mode`、自定义 `user:` 一律禁止。因此容器内默认 root 运行是常态，别写 `user:`。
- **`external` 网络/卷**、卷 `driver_opts`、`deploy.resources.reservations.devices`（GPU）禁止。
- **挂载边界**：bind 源路径必须落在 Lab 工作目录内（相对路径从 compose 文件所在目录解析）；禁止挂 `docker.sock` 等容器运行时 socket、`/proc`、`/sys`、`/dev`，以及工作目录下的 `.secrets/`。
- **远程 build context**：仅允许 `https://`，且不能自定义 dockerfile 路径。
- 任何路径字段含 `$` 或 `~` 直接拒绝。

## 常见问题速查

### `Connection refused`（redis / mysql / mq / minio 反复刷屏）

两种根因，修法不同：

1. **compose 里根本没有该服务**：应用代码在连一个不存在的中间件。回源码查依赖指纹（ioredis/bullmq/mysql 驱动/celery…），补官方镜像服务（见 existing-image.md 的中间件表）。
2. **有服务但连的是 `localhost`/`127.0.0.1` 或宿主映射端口**：容器内必须用 **compose 服务名 + 容器端口**（`REDIS_HOST: redis`、`6379`），并在 compose `environment` 显式覆盖 `.env.example` 的宿主机开发默认值。

配套：应用 `depends_on` 依赖服务 + 依赖侧 healthcheck；启动期竞态靠 healthy 控序解决，不要靠应用重试硬扛。

### 数据库连不上 / 首页报"缺表"

- 应用比数据库先起：`depends_on: {db: {condition: service_healthy}}`。
- 账号/库未初始化：官方镜像的环境变量自动建库（`MYSQL_DATABASE`/`MYSQL_ROOT_PASSWORD`/`POSTGRES_*`）；建表/种子脚本挂 `/docker-entrypoint-initdb.d/`，或启动命令先跑迁移（如 `prisma db push`、`flyway migrate`）。
- MySQL 常见：`--character-set-server=utf8mb4`。

### 应用起了但探活 404 / 连接被拒

- 应用监听 `127.0.0.1`：容器内必须监听 `0.0.0.0`。平台只认「TCP + 非 127.0.0.1/::1 绑定」的端口为可用 Web 入口——只绑 127.0.0.1 或只发布 UDP 会被判「无可供复现容器访问的 Web 绑定」。
- context path：Spring `server.servlet.context-path` 存在时，平台探活路径要带前缀（`target_url` 占位路径写全）。
- 前端 SPA：nginx 需 `try_files $uri /index.html`。

### 健康检查一直 unhealthy

- healthcheck 命令的端点不存在：换成真实存在的路径。
- 镜像里没有 `curl`/`wget`：装一个，或用语言自带探测。
- `start_period` 太短：重型应用（Spring/Java、首启跑大库迁移）给足 300s，轻量应用 60s。

### 构建期依赖装不上 / 慢（npm ETIMEDOUT / Could not transfer）

- 换国内镜像源（npm/pip/maven/apt）。
- 缓存挂载（见 dockerfile-patterns.md 的 `--mount=type=cache`）。
- COPY 了 lockfile 用 `npm ci`，构建才可复现。

### Maven cache mount 两个经典坑

- **`settings file does not exist`**：`--mount=type=cache,target=/root/.m2` 会遮盖前序 `COPY settings.xml`。修法：同一 RUN 内、mount 之后先 `cp` settings 再 mvn。
- **`Device or resource busy`**：cache mount 落在 mvn 会清理的目录（`target`）上导致 clean 失败。cache 只用于纯下载目录（`/root/.m2`），不要盖 `target`/`node_modules`。

### Java JDK 版本不匹配 (`UnsupportedClassVersionError` / `JAXBException`)

- **`UnsupportedClassVersionError: ... has been compiled by a more recent version of the Java Runtime`**：
  编译使用的 JDK 高于运行镜像的 JRE。修法：保持 builder 与 runtime 的 JDK 主版本严格一致（JDK 8/11/17/21）。
- **`NoClassDefFoundError: javax/xml/bind/JAXBException`** 或 `java.lang.NoClassDefFoundError: sun/misc/Unsafe`：
  项目基于老旧 Java 8 编写，但在 JDK 9+ 运行。修法：Dockerfile 基础镜像降级为 `openjdk-8` / `temurin-8`。

### Java WAR 包在 Tomcat 访问 404 / 缺少 context-path

- WAR 包放入 Tomcat `webapps/` 后，默认 context-path 即为 WAR 文件名（如 `scada-lts.war` → 路径为 `/scada-lts`）。
- **最稳妥修法**：将 WAR 包拷贝为 `/usr/local/tomcat/webapps/ROOT.war`，让根路径 `/` 即可访问；若项目硬编码了 `/Scada-LTS` 等 context-path，在 `submit_result` 时将 `target_url` 占位写为完整路径（如 `http://target-placeholder:8080/Scada-LTS`）。

### MySQL 8.0 认证插件报错 (`caching_sha2_password cannot be loaded`)

- 早期 Java/PHP 客户端不支持 MySQL 8.0 默认加密插件。
- 修法：在 MySQL compose service 添加 command：`command: --default-authentication-plugin=mysql_native_password`。

### PHP 传统 CMS 报缺函数 / 目录不可写

- **`Call to undefined function mysqli_connect()` / `pdo_mysql` / `mb_detect_encoding()`**：
  官方 `php:fpm-alpine` 默认不带这些扩展。Dockerfile 必须通过 `docker-php-ext-install` 安装 `mysqli pdo_mysql gd zip mbstring bcmath xml`。
- **`Fatal error: Call to undefined function mysql_connect()`**：
  老旧项目使用 PHP 5.x 的 `mysql_*` 函数。修法：Dockerfile 基础镜像改用 `php:5.6-fpm-alpine` 并安装 `mysql` 扩展。
- **安装向导提示 "Directory is not writable: cache/runtime/config"**：
  在 Dockerfile 中执行 `RUN chmod -R 777 /var/www/html` 赋予完全写权限。

### Node.js 18+ 构建报错 (`ERR_OSSL_EVP_UNSUPPORTED` / `digital envelope routines::unsupported`)

- Webpack 4 / Vue CLI 2-3 / node-sass 在 Node 17+ 的 OpenSSL 3.0 下报错。
- 修法：在 Dockerfile 构建阶段设置 `ENV NODE_OPTIONS="--openssl-legacy-provider --max-old-space-size=4096"`。

### Python `pip install` 编译 C 扩展报错 (`Python.h: No such file` / `pg_config executable not found`)

- 安装 `psycopg2`, `mysqlclient`, `Pillow`, `cryptography`, `lxml` 时缺少系统库。
- 修法：在 Dockerfile 编译阶段预先 `apt-get install -y gcc g++ libpq-dev default-libmysqlclient-dev libssl-dev libffi-dev libxml2-dev libxslt1-dev zlib1g-dev libjpeg-dev`。

### MySQL 导入初始表报大包截断 (`Packet for query is too large`)

- 初始 SQL 数据文件单条语句或表数据超过默认 16MB。
- 修法：在 MySQL compose service 添加 `--max_allowed_packet=64M`。

### Nginx 探活或安装向导报 `504 Gateway Timeout`

- 首次执行数据库迁移或安装向导需要超过 60s。
- 修法：在 Nginx 配置文件 `location ~ \.php$` 块中添加 `fastcgi_read_timeout 300;`。

### 宿主特权依赖 / Docker-in-Docker 调度平台启动失败

- **现象**：目标项目在容器内尝试执行 `docker ps` 或连接 `/var/run/docker.sock` 报错 `No such file or directory` 或 `Permission denied`。
- **第一性原理取舍**：**严禁挂载宿主 socket！**
- **修法**：在 compose `environment` 中通过环境变量禁用底层特权调度（如 `DOCKER_ENABLED=false`、`EXECUTION_MODE=mock`、`LOCAL_MODE=true`），或者仅启动前端/Web/API 容器，使 Web 路由和业务接口可正常探测。

### COPY 静默不生效

BuildKit `progress=plain` 下 COPY 日志有时不显示，但文件实际已拷贝。判断依据是构建产物路径是否存在（可在 Dockerfile 里加 `RUN ls` 自证），不要凭日志缺失反复加 COPY。

### 宿主端口占用

只改 compose 的 host 侧映射口（`18080:8080`），容器内监听口不动；避开 JSON 里的 `occupied_host_ports`。

## 纪律

- 一次只改一个变量，改完提交让平台重试，凭 `previous_error` 的变化判断是否有效。
- 不要因为构建网络抖动而改启动拓扑（合容器 / 换架构）。
- 排障轮数上限由平台设置控制（`env_ready_max_attempts`，默认 5 轮）；把每轮改动聚焦在 highest-confidence 的一处。
