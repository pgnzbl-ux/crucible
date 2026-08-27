# Dockerfile 模式参考

各语言多阶段构建模板。env_ready 场景补三条平台约定：进程日志打 stdout/stderr；不要在镜像里装 Docker；只把 Web 入口端口留给 compose 映射。其余原则与模板通用。

## 通用原则

- **多阶段构建**：构建依赖与运行时分离，减小最终镜像体积
- **非 root 运行**：创建专用用户，避免以 root 跑业务进程
- **健康检查**：镜像内 `HEALTHCHECK` 或 compose 层 healthcheck，供 `depends_on` 控序
- **时区**：默认 `Asia/Shanghai`，可用构建参数覆盖
- **合并 RUN**：减少层数，每层末尾清缓存
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
RUN apt-get update && apt-get install -y --no-install-recommends curl tini \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /app /app
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:3000/ || exit 1
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["npm", "run", "start"]
```

说明：

- `npm ci` 严格按 lockfile 安装，构建可复现
- `tini` 作 PID 1 处理信号；平台要求业务进程不是孤儿（PID 1 语义）
- Next.js `output: 'standalone'` 用 `node server.js`；Nuxt 用 `node .output/server/index.mjs`；需要 concurrently 起 worker 的项目保持其原 `npm run start`

## Python 模板

```dockerfile
# syntax=docker/dockerfile:1
FROM python:3.12-slim AS builder
WORKDIR /app
COPY pyproject.toml requirements.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir -r requirements.txt

FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY . .
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:8000/ || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

说明：Flask 改 `CMD ["flask", "run", "--host", "0.0.0.0"]`；Django 改 `gunicorn project.wsgi:application`。

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
COPY --from=builder /build/target/*.jar /app/app.jar
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -f http://localhost:8080/ || exit 1
ENTRYPOINT ["sh", "-c", "java $JAVA_OPTS -jar /app/app.jar"]
```

说明：Gradle 换 `gradle:8-jdk21` + `gradle build -x test`；`MaxRAMPercentage` 让 JVM 按容器限额分配堆；有 Actuator 用 `/actuator/health`，否则退化为首页/TCP 探测。

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
RUN apk add --no-cache ca-certificates tzdata curl
WORKDIR /app
COPY --from=builder /app /app/app
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8080/ || exit 1
CMD ["./app"]
```

说明：`CGO_ENABLED=0` 静态二进制配 alpine；多个 main 包时调整 `-o` 路径。

## PHP 模板

```dockerfile
# syntax=docker/dockerfile:1
FROM php:8.3-fpm-alpine AS runtime
ENV TZ=Asia/Shanghai
RUN apk add --no-cache nginx curl tzdata \
    && docker-php-ext-install pdo_mysql opcache
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
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD curl -f http://localhost/ || exit 1
CMD ["sh", "-c", "php-fpm -D && nginx -g 'daemon off;'"]
```

说明：Laravel `root` 指向 `public`，构建阶段需 `composer install`。

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
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl tini
WORKDIR /app
COPY --from=builder /src/target/release/app /app/app
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8080/ || exit 1
CMD ["./app"]
```

说明：先编空 main 缓存依赖再编真实代码；运行镜像用 debian-slim（glibc 兼容）优于 alpine。

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
.idea
.vscode
coverage
*.log
```
