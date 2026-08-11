# Dockerfile 模式参考

本文件提供各语言的多阶段构建 Dockerfile 模板。所有模板遵循统一原则：多阶段构建、非 root 用户、OCI 标签、健康检查、最小镜像。`scripts/init_project.py` 据此生成。

## 通用原则

- **多阶段构建**：构建依赖与运行时分离，减小最终镜像体积
- **非 root 运行**：创建专用用户，避免以 root 运行容器
- **OCI 标签**：标注 `org.opencontainers.image.*` 元数据
- **健康检查**：内置 `HEALTHCHECK`，便于宝塔与编排器监控
- **时区**：默认 `Asia/Shanghai`，可通过构建参数覆盖
- **合并 RUN**：减少镜像层数，每层末尾清理缓存
- **.dockerignore**：排除无关文件加速构建

## Node.js 模板

```dockerfile
# syntax=docker/dockerfile:1
FROM node:20-slim AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM node:20-slim AS runtime
ENV NODE_ENV=production \
    TZ=Asia/Shanghai
WORKDIR /app
RUN groupadd -r app && useradd -r -g app app \
    && apt-get update && apt-get install -y --no-install-recommends curl tini \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /app /app
RUN chown -R app:app /app
USER app
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:3000/health || exit 1
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["node", "server.js"]
```

说明：

- `npm ci` 严格按 lockfile 安装，构建可复现
- `tini` 作为 PID 1 处理信号，避免僵尸进程
- Next.js 用 `node server.js`（需 `output: 'standalone'`）；Nuxt 用 `node .output/server/index.mjs`

## Python 模板

```dockerfile
# syntax=docker/dockerfile:1
FROM python:3.12-slim AS builder
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml requirements.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir -r requirements.txt

FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai
WORKDIR /app
RUN groupadd -r app && useradd -r -g app app \
    && apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY . .
RUN chown -R app:app /app
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

说明：

- 构建阶段用 pip 缓存挂载加速
- `PYTHONUNBUFFERED` 确保日志实时输出
- Flask 改 `CMD ["flask", "run", "--host", "0.0.0.0"]`；Django 改 `CMD ["gunicorn", "project.wsgi:application"]`

## Java 模板

```dockerfile
# syntax=docker/dockerfile:1
FROM maven:3.9-eclipse-temurin-21 AS builder
WORKDIR /build
COPY pom.xml .
RUN --mount=type=cache,target=/root/.m2 mvn dependency:go-offline
COPY src ./src
RUN --mount=type=cache,target=/root/.m2 mvn package -DskipTests

FROM eclipse-temurin:21-jre AS runtime
ENV TZ=Asia/Shanghai \
    JAVA_OPTS="-XX:MaxRAMPercentage=75.0"
WORKDIR /app
RUN groupadd -r app && useradd -r -g app app \
    && apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /build/target/*.jar /app/app.jar
RUN chown -R app:app /app
USER app
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -f http://localhost:8080/actuator/health || exit 1
ENTRYPOINT ["sh", "-c", "java $JAVA_OPTS -jar /app/app.jar"]
```

说明：

- Gradle 项目把构建阶段换为 `FROM gradle:8-jdk21`，命令换 `gradle build -x test`
- `MaxRAMPercentage` 让 JVM 按容器内存限制自动分配堆
- 健康检查用 Spring Boot Actuator；无 Actuator 时改用 TCP 探测或自定义端点

## Go 模板

```dockerfile
# syntax=docker/dockerfile:1
FROM golang:1.23 AS builder
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -ldflags="-s -w" -o /app ./...

FROM alpine:3.20 AS runtime
ENV TZ=Asia/Shanghai
RUN apk add --no-cache ca-certificates tzdata curl \
    && addgroup -S app && adduser -S app -G app
WORKDIR /app
COPY --from=builder /app /app/app
USER app
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8080/health || exit 1
CMD ["./app"]
```

说明：

- `CGO_ENABLED=0` 生成静态二进制，运行镜像可用 alpine/distroless
- `-ldflags="-s -w"` 去除调试信息减小体积
- 多个 main 包时调整 `-o` 输出路径

## PHP 模板

```dockerfile
# syntax=docker/dockerfile:1
FROM php:8.3-fpm-alpine AS runtime
ENV TZ=Asia/Shanghai
RUN apk add --no-cache nginx curl tzdata \
    && docker-php-ext-install pdo_mysql opcache \
    && addgroup -S app && adduser -S app -G app
WORKDIR /var/www/html
COPY . .
COPY <<'EOF' /etc/nginx/http.d/default.conf
server {
    listen 80;
    root /var/www/html/public;
    index index.php;
    location ~ \.php$ {
        fastcgi_pass 127.0.0.1:9000;
        fastcgi_index index.php;
        include fastcgi_params;
        fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name;
    }
}
EOF
RUN chown -R app:app /var/www/html
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD curl -f http://localhost/ || exit 1
CMD ["sh", "-c", "php-fpm -D && nginx -g 'daemon off;'"]
```

说明：

- Laravel 项目 `root` 指向 `public`，需 `composer install` 构建阶段
- 用 supervisord 或上述 `sh -c` 同时拉起 php-fpm 与 nginx

## Rust 模板

```dockerfile
# syntax=docker/dockerfile:1
FROM rust:1.82 AS builder
WORKDIR /src
COPY Cargo.toml Cargo.lock ./
RUN mkdir src && echo "fn main() {}" > src/main.rs && cargo build --release
COPY . .
RUN cargo build --release

FROM debian:bookworm-slim AS runtime
ENV TZ=Asia/Shanghai
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r app && useradd -r -g app app
WORKDIR /app
COPY --from=builder /src/target/release/app /app/app
USER app
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8080/health || exit 1
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["./app"]
```

说明：

- 先编译空 main 缓存依赖，再编译真实代码，加速增量构建
- 运行镜像用 debian-slim 而非 alpine（glibc 兼容性）

## 静态站点模板

```dockerfile
# syntax=docker/dockerfile:1
FROM node:20-slim AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine AS runtime
ENV TZ=Asia/Shanghai
COPY --from=builder /app/dist /usr/share/nginx/html
COPY <<'EOF' /etc/nginx/conf.d/default.conf
server {
    listen 80;
    root /usr/share/nginx/html;
    index index.html;
    location / { try_files $uri $uri/ /index.html; }
}
EOF
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD curl -f http://localhost/ || exit 1
```

## OCI 标签（所有模板追加）

```dockerfile
LABEL org.opencontainers.image.title="${PROJECT_NAME}" \
      org.opencontainers.image.description="${PROJECT_DESC}" \
      org.opencontainers.image.source="https://github.com/<org>/<repo>" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${VERSION}"
```

`init_project.py` 生成时用 `docker-publish.yaml` 的值填充。

## .dockerignore 通用模板

```
.git
.gitignore
node_modules
dist
build
target
__pycache__
*.pyc
.venv
venv
.env
.env.*
!.env.example
*.md
Dockerfile*
docker-compose*
docker-publish.yaml
.idea
.vscode
coverage
*.log
```
