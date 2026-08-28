# Dockerfile 模式参考

各语言多阶段构建模板的**唯一模板库**——framework-cookbooks.md 的场景打法直接引用这里的模板，不另存副本。env_ready 场景补三条平台约定：进程日志打 stdout/stderr；不要在镜像里装 Docker；只把 Web 入口端口留给 compose 映射。其余原则与模板通用。

## 通用原则

- **多阶段构建**：构建依赖与运行时分离，减小最终镜像体积
- **运行身份**：靶场以可用性优先，默认 root 运行即可——compose 策略禁止自定义 `user:`，安装向导类项目还需要全目录可写，不要为非 root 增加复杂度
- **健康检查**：镜像内 `HEALTHCHECK` 或 compose 层 healthcheck，供 `depends_on` 控序
- **时区**：默认 `Asia/Shanghai`，可用构建参数覆盖
- **合并 RUN**：减少层数，每层末尾清缓存
- **.dockerignore**：排除无关文件加速构建

## Node.js 模板

```dockerfile
# syntax=docker/dockerfile:1
FROM node:20-slim AS builder
ENV NODE_OPTIONS="--openssl-legacy-provider --max-old-space-size=4096"
WORKDIR /app
COPY package*.json ./
RUN npm ci || npm install
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

- `npm ci` 严格按 lockfile 安装，构建可复现；若报错切换 `npm install`
- `NODE_OPTIONS="--openssl-legacy-provider"` 解决 Node 18+ 对旧项目 Webpack 4 / vue-cli 报 `ERR_OSSL_EVP_UNSUPPORTED`
- `tini` 作 PID 1 处理信号；平台要求业务进程不是孤儿（PID 1 语义）
- Next.js `output: 'standalone'` 用 `node server.js`；Nuxt 用 `node .output/server/index.mjs`；需要 concurrently 起 worker 的项目保持其原 `npm run start`

## Python 模板

```dockerfile
# syntax=docker/dockerfile:1
FROM python:3.12-slim AS builder
WORKDIR /app
# 安装常见 C 扩展底层编译库（解决 psycopg2, mysqlclient, Pillow, cryptography 编译失败）
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc g++ libpq-dev default-libmysqlclient-dev libssl-dev libffi-dev \
        libxml2-dev libxslt1-dev zlib1g-dev libjpeg-dev \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml requirements.txt* ./
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

## Java 模板 1：Spring Boot 可执行 JAR

```dockerfile
# syntax=docker/dockerfile:1
FROM maven:3.9-eclipse-temurin-17 AS builder
WORKDIR /build
COPY pom.xml .
RUN --mount=type=cache,target=/root/.m2 mvn dependency:go-offline -B || true
COPY src ./src
RUN --mount=type=cache,target=/root/.m2 mvn clean package -DskipTests -B

FROM eclipse-temurin:17-jre AS runtime
ENV TZ=Asia/Shanghai \
    JAVA_OPTS="-XX:MaxRAMPercentage=75.0"
WORKDIR /app
COPY --from=builder /build/target/*.jar /app/app.jar
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD curl -f http://localhost:8080/ || exit 1
ENTRYPOINT ["sh", "-c", "java $JAVA_OPTS -jar /app/app.jar"]
```

## Java 模板 2：传统 Web / WAR 包 on Tomcat (Scada-LTS / RuoYi-WAR)

> **极重要**：当 `pom.xml` 中 `<packaging>war</packaging>` 时，**严禁使用 `java -jar`**！必须部署在 Tomcat 容器中。对于 JDK 8 老旧项目，必须使用 JDK 8 编译和运行。

```dockerfile
# syntax=docker/dockerfile:1
FROM maven:3.8.6-openjdk-8 AS builder
WORKDIR /build
COPY pom.xml .
RUN mvn dependency:go-offline -B || true
COPY . .
RUN mvn clean package -DskipTests -B

FROM tomcat:9.0-jdk8-temurin AS runtime
ENV TZ=Asia/Shanghai \
    JAVA_OPTS="-Xms256m -Xmx1024m -XX:+UseG1GC"
WORKDIR /usr/local/tomcat
RUN rm -rf webapps/*
# 拷贝 WAR 包至 webapps/ROOT.war（根路径可达）
COPY --from=builder /build/target/*.war /usr/local/tomcat/webapps/ROOT.war
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=5 \
  CMD curl -f http://localhost:8080/ || exit 1
CMD ["catalina.sh", "run"]
```

## Java 模板 3：多模块 Maven 工程

```dockerfile
# syntax=docker/dockerfile:1
FROM maven:3.9-eclipse-temurin-8 AS builder
WORKDIR /build
COPY . .
# 根目录下全量构建，并只取主模块产物
RUN mvn clean package -DskipTests -B

FROM eclipse-temurin:8-jre AS runtime
ENV TZ=Asia/Shanghai \
    JAVA_OPTS="-XX:MaxRAMPercentage=75.0 -Djava.security.egd=file:/dev/./urandom"
WORKDIR /app
COPY --from=builder /build/*/target/*.jar /app/app.jar
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD curl -f http://localhost:8080/ || exit 1
ENTRYPOINT ["sh", "-c", "java $JAVA_OPTS -jar /app/app.jar"]
```

## Java 模板 4：Gradle 工程

```dockerfile
# syntax=docker/dockerfile:1
FROM gradle:8-jdk17 AS builder
WORKDIR /build
COPY . .
# 关键：必须加 --no-daemon 避免 Gradle 派生常驻进程导致构建卡死，加 -x test 跳过单元测试
RUN gradle build --no-daemon -x test

FROM eclipse-temurin:17-jre AS runtime
ENV TZ=Asia/Shanghai \
    JAVA_OPTS="-XX:MaxRAMPercentage=75.0"
WORKDIR /app
COPY --from=builder /build/build/libs/*.jar /app/app.jar
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD curl -f http://localhost:8080/ || exit 1
ENTRYPOINT ["sh", "-c", "java $JAVA_OPTS -jar /app/app.jar"]
```

## Go 模板

```dockerfile
# syntax=docker/dockerfile:1
FROM golang:1.23 AS builder
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -ldflags="-s -w" -o /app/server ./...

FROM alpine:3.20 AS runtime
ENV TZ=Asia/Shanghai
RUN apk add --no-cache ca-certificates tzdata curl
WORKDIR /app
COPY --from=builder /app/server /app/app/server
# 静态资源与模板目录（若项目有）
COPY --from=builder /src/static /app/static
COPY --from=builder /src/views /app/views
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8080/ || exit 1
CMD ["./server"]
```

说明：`CGO_ENABLED=0` 静态二进制配 alpine；多个 main 包时调整 `-o` 路径；项目没有 `static/`/`views/` 目录时删掉对应 COPY。

## PHP 模板 1：传统 CMS / Web 安装向导 (Discuz / 禅道 / WordPress / DedeCMS)

```dockerfile
# syntax=docker/dockerfile:1
FROM php:7.4-fpm-alpine AS runtime
ENV TZ=Asia/Shanghai

RUN apk add --no-cache \
        nginx curl tzdata freetype-dev libjpeg-turbo-dev \
        libpng-dev libzip-dev oniguruma-dev libxml2-dev \
    && docker-php-ext-configure gd --with-freetype --with-jpeg \
    && docker-php-ext-install -j$(nproc) \
        pdo_mysql mysqli gd zip mbstring bcmath xml opcache

WORKDIR /var/www/html
COPY . .

# 关键：赋予完整写入权限供安装向导/运行时写配置和缓存
RUN chmod -R 777 /var/www/html

COPY <<'EOF' /etc/nginx/http.d/default.conf
server {
    listen 80;
    root /var/www/html;
    index index.php index.html;
    client_max_body_size 100M;

    location / {
        try_files $uri $uri/ /index.php?$query_string;
    }

    location ~ [^/]\.php(/|$) {
        fastcgi_split_path_info ^(.+?\.php)(/.*)$;
        if (!-f $document_root$fastcgi_script_name) { return 404; }
        fastcgi_pass 127.0.0.1:9000;
        fastcgi_index index.php;
        include fastcgi_params;
        fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name;
        fastcgi_param PATH_INFO $fastcgi_path_info;
        fastcgi_read_timeout 300;
    }
}
EOF

EXPOSE 80
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD curl -f http://localhost/ || exit 1
CMD ["sh", "-c", "php-fpm -D && nginx -g 'daemon off;'"]
```

## PHP 模板 2：现代框架 (Laravel / ThinkPHP)

```dockerfile
# syntax=docker/dockerfile:1
FROM composer:2 AS vendor
WORKDIR /app
COPY composer.json composer.lock* ./
RUN composer install --no-dev --no-scripts --prefer-dist --ignore-platform-reqs || true
COPY . .
RUN composer dump-autoload --optimize

FROM php:8.2-fpm-alpine AS runtime
RUN apk add --no-cache nginx curl tzdata \
    && docker-php-ext-install pdo_mysql opcache
WORKDIR /var/www/html
COPY --from=vendor /app /var/www/html
RUN chmod -R 777 /var/www/html/storage /var/www/html/runtime || true
COPY <<'EOF' /etc/nginx/http.d/default.conf
server {
    listen 80;
    root /var/www/html/public;
    index index.php;
    location / { try_files $uri $uri/ /index.php?$query_string; }
    location ~ \.php$ {
        fastcgi_pass 127.0.0.1:9000;
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

## PHP 模板 3：5.6 遗留老旧 CMS (源码含 `mysql_connect` / 短标签 `<?`)

```dockerfile
# syntax=docker/dockerfile:1
FROM php:5.6-fpm-alpine AS runtime
ENV TZ=Asia/Shanghai

RUN apk add --no-cache nginx curl tzdata freetype-dev libjpeg-turbo-dev libpng-dev \
    && docker-php-ext-configure gd --with-freetype-dir=/usr/include/ --with-jpeg-dir=/usr/include/ \
    && docker-php-ext-install -j$(nproc) mysql mysqli pdo_mysql gd \
    && echo "short_open_tag = On" >> /usr/local/etc/php/conf.d/docker-php-ext-custom.ini \
    && echo "date.timezone = Asia/Shanghai" >> /usr/local/etc/php/conf.d/docker-php-ext-custom.ini

WORKDIR /var/www/html
COPY . .
RUN chmod -R 777 /var/www/html

COPY <<'EOF' /etc/nginx/http.d/default.conf
server {
    listen 80;
    root /var/www/html;
    index index.php index.html;
    client_max_body_size 100M;
    location / { try_files $uri $uri/ /index.php?$query_string; }
    location ~ \.php$ {
        fastcgi_pass 127.0.0.1:9000;
        include fastcgi_params;
        fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name;
        fastcgi_read_timeout 300;
    }
}
EOF
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD curl -f http://localhost/ || exit 1
CMD ["sh", "-c", "php-fpm -D && nginx -g 'daemon off;'"]
```

## .NET 模板 (ASP.NET Core)

```dockerfile
# syntax=docker/dockerfile:1
FROM mcr.microsoft.com/dotnet/sdk:8.0 AS builder
WORKDIR /src
COPY *.sln .
COPY *.csproj ./
RUN dotnet restore || true
COPY . .
RUN dotnet publish -c Release -o /app/publish

FROM mcr.microsoft.com/dotnet/aspnet:8.0 AS runtime
ENV ASPNETCORE_URLS=http://+:8080 \
    TZ=Asia/Shanghai
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /app/publish .
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:8080/ || exit 1
ENTRYPOINT ["dotnet", "App.dll"]
```

## Rust 模板

```dockerfile
# syntax=docker/dockerfile:1
FROM rust:1.82 AS builder
WORKDIR /src
COPY Cargo.toml Cargo.lock ./
# 预先编译空工程以缓存依赖；首次编译允许失败（可能缺 src/bin 等真实文件）
RUN mkdir src && echo "fn main() {}" > src/main.rs && cargo build --release || true
COPY . .
RUN touch src/main.rs && cargo build --release

FROM debian:bookworm-slim AS runtime
ENV TZ=Asia/Shanghai
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl tini \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=builder /src/target/release/app /app/app
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8080/ || exit 1
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["./app"]
```

## Ruby on Rails 模板

```dockerfile
# syntax=docker/dockerfile:1
FROM ruby:3.2-slim AS runtime
ENV RAILS_ENV=production \
    RAILS_SERVE_STATIC_FILES=true \
    RAILS_LOG_TO_STDOUT=true \
    TZ=Asia/Shanghai

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev default-libmysqlclient-dev nodejs yarn curl \
    && rm -rf /var/lib/apt/lists/*

COPY Gemfile Gemfile.lock ./
RUN bundle config set --local without 'development test' \
    && bundle install --jobs 4 --retry 3

COPY . .
RUN bundle exec rake assets:precompile || true

EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -f http://localhost:3000/ || exit 1

CMD ["sh", "-c", "bundle exec rake db:migrate && bundle exec rails server -b 0.0.0.0 -p 3000"]
```

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
# nginx:alpine 不自带 curl，HEALTHCHECK 依赖它
RUN apk add --no-cache curl
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
