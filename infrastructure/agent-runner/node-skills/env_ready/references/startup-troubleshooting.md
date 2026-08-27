# 失败对症排障参考

平台闭环里你**没有 Docker CLI、也看不到完整日志**——排障输入是三样：`failed_stage`（哪一层失败）、`previous_error`（平台捕获的构建/启动/探活错误片段）、以及源码本身。本参考把"现象 → 根因层级 → 改哪里"固化成速查表；纪律仍是一次只改一处。

## 根因层级 × failed_stage 对照

| failed_stage | 层级 | 典型现象 | 改哪里 |
|---|---|---|---|
| `compose_build` | 构建期 | 依赖装不上、编译错误、COPY 路径错 | Dockerfile / build.context |
| `container_start` | 编排期 | service 起不来、互相连不上、启动顺序错 | docker-compose.yml |
| `container_healthcheck` | 编排/应用期 | 容器起了但健康检查不过 | healthcheck 定义 / 应用配置 |
| `health_check` | 应用期 | HTTP 通了但正文崩溃（Fatal/缺表/Whitelabel/traceback） | 应用配置 / 迁移 / 环境变量 |
| `compose_policy` | 策略期 | 安全策略拒绝 | 去掉 privileged / host 网络 / 越界 mount |

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

- 应用监听 `127.0.0.1`：容器内必须监听 `0.0.0.0`。
- context path：Spring `server.servlet.context-path` 存在时，平台探活路径要带前缀（`target_url` 占位路径写全）。
- 前端 SPA：nginx 需 `try_files $uri /index.html`。

### 健康检查一直 unhealthy

- healthcheck 命令的端点不存在：换成真实存在的路径。
- 镜像里没有 `curl`/`wget`：装一个，或用语言自带探测。
- `start_period` 太短：Java 等慢启动给足（60s）。

### 构建期依赖装不上 / 慢（npm ETIMEDOUT / Could not transfer）

- 换国内镜像源（npm/pip/maven/apt）。
- 缓存挂载（见 dockerfile-patterns.md 的 `--mount=type=cache`）。
- COPY 了 lockfile 用 `npm ci`，构建才可复现。

### Maven cache mount 两个经典坑

- **`settings file does not exist`**：`--mount=type=cache,target=/root/.m2` 会遮盖前序 `COPY settings.xml`。修法：同一 RUN 内、mount 之后先 `cp` settings 再 mvn。
- **`Device or resource busy`**：cache mount 落在 mvn 会清理的目录（`target`）上导致 clean 失败。cache 只用于纯下载目录（`/root/.m2`），不要盖 `target`/`node_modules`。

### COPY 静默不生效

BuildKit `progress=plain` 下 COPY 日志有时不显示，但文件实际已拷贝。判断依据是构建产物路径是否存在（可在 Dockerfile 里加 `RUN ls` 自证），不要凭日志缺失反复加 COPY。

### 宿主端口占用

只改 compose 的 host 侧映射口（`18080:8080`），容器内监听口不动；避开 JSON 里的 `occupied_host_ports`。

## 纪律

- 一次只改一个变量，改完提交让平台重试，凭 `previous_error` 的变化判断是否有效。
- 不要因为构建网络抖动而改启动拓扑（合容器 / 换架构）。
- 排障轮数上限由平台控制（5 轮）；把每轮改动聚焦在 highest-confidence 的一处。
