# 查找可直接 pull 的现成镜像

第 3 步。自己写 Dockerfile 成本高且易错，**能用现成镜像就用现成的**。本文件说明如何判断项目是否有可直接 `docker pull` 的可信镜像。

## 判断顺序

### 1. 项目是否自带引用现成镜像的 compose

看根目录 `docker-compose.yml` / `docker-compose.*.yml` / `deploy/`、`docker/` 目录。如果里面 `image:` 指向了发布好的镜像（而非 `build:` 本地构建），说明官方已提供镜像，**直接复用这份 compose**是最省事的路径。

### 2. README / 官方文档是否给出镜像与运行命令

搜 README 及 docs 里的关键词：`docker pull`、`docker run`、`docker-compose`、`image:`、Docker Hub / GHCR 链接。很多成熟开源项目会写明：

```
docker pull org/project:tag
docker run -d -p 8080:8080 org/project
```

有则直接采用官方给的运行方式。

### 3. 项目本身就是"某个有官方镜像的知名软件"

很多系统本身在 Docker Hub / GHCR 有一等公民官方镜像，例如：`nginx`、`mysql`、`postgres`、`redis`、`nextcloud`、`gitea/gitea`、`grafana/grafana`、`wordpress` 等。如果拿到的源码正是这类软件的源码，且目标只是"跑起来看看"，用官方镜像比从源码编译快得多。

### 4. 依赖的中间件一律用官方镜像

无论应用本体是否自建，它依赖的**数据库、缓存、消息队列、搜索引擎**等中间件，**永远用官方镜像**作为 compose 里的独立 service，绝不从源码编译，也绝不装到宿主机：

| 中间件 | 官方镜像 |
|--------|----------|
| MySQL | `mysql:8` |
| PostgreSQL | `postgres:16` |
| Redis | `redis:7` |
| MongoDB | `mongo:7` |
| RabbitMQ | `rabbitmq:3-management` |
| Elasticsearch | `elasticsearch:8.x` |
| MinIO | `minio/minio` |
| Nginx | `nginx:alpine` |

## 可信度判断

用现成镜像前确认可信：

- **优先官方镜像**（Docker Hub Official Images、软件官方组织的命名空间）。
- 社区镜像看 stars/pulls、是否活跃维护、Dockerfile 是否公开。
- 版本标签**避免只用 `latest`**，尽量锁定明确版本，保证可复现。
- 拿不准可信度或涉及付费/私有镜像时，向用户说明并让其决定。

## 判断结果

- **有可信现成镜像**（应用本体或整套 compose）→ 走 SKILL.md 第 4 步"分支 A/B"，不写 Dockerfile。
- **只有中间件有现成镜像，应用本体需从源码构建** → 应用走"分支 C"自建 Dockerfile，中间件用官方镜像组进同一份 compose。
- **完全没有现成镜像** → 应用与所有服务都走自建，参照 dockerfile-patterns.md。
