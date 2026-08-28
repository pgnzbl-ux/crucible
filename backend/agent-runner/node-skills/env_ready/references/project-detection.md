# 项目类型检测规则

从项目根目录的文件特征判定语言与框架，用于选择 Dockerfile 模板、默认端口与健康检查方式。recon 阶段先用本表定主语言，再用框架细分修正端口与启动命令。

## 检测优先级

按以下顺序匹配，命中即停止。检测基于文件存在性与内容关键字。

## 检测规则表

| 语言 | 触发文件 / 关键字 | 默认基础镜像 | 默认端口 | 健康检查 |
|------|-------------------|--------------|----------|----------|
| nodejs | `package.json` | `node:<lts>-slim` | 3000 | `curl -f http://localhost:3000/health` |
| python | `requirements.txt` / `pyproject.toml` / `setup.py` / `Pipfile` | `python:3.12-slim` | 8000 | `curl -f http://localhost:8000/health` |
| java | `pom.xml` / `build.gradle` / `build.gradle.kts` | `eclipse-temurin:21-jre` | 8080 | `curl -f http://localhost:8080/actuator/health` |
| go | `go.mod` | `golang:1.23` 构建 + `alpine` 运行 | 8080 | `curl -f http://localhost:8080/health` |
| php | `composer.json` / `index.php` / `artisan` | `php:8.3-fpm-alpine` + nginx | 80 | `curl -f http://localhost/` |
| rust | `Cargo.toml` | `rust:1.82` 构建 + `debian:bookworm-slim` 运行 | 8080 | `curl -f http://localhost:8080/health` |
| dotnet | `*.csproj` / `*.sln` | `mcr.microsoft.com/dotnet/sdk:8.0` 构建 + `aspnet:8.0` 运行 | 8080 | `curl -f http://localhost:8080/` |
| ruby | `Gemfile` / `config.ru` | `ruby:3.2-slim` | 3000 | `curl -f http://localhost:3000/` |
| static | 仅 `index.html` / `*.html` 无后端 | `nginx:alpine` | 80 | `curl -f http://localhost/` |
| other | 以上均不匹配 | `debian:bookworm-slim` | 8080 | 无 |

## 框架细分（用于细化端口与入口）

在语言判定后，进一步检测框架以修正默认值：

### Node.js

| 框架 | 检测关键字（package.json dependencies / scripts） | 端口 | 启动命令 |
|------|---------------------------------------------------|------|----------|
| Next.js | `next` | 3000 | `node server.js` 或 `next start` |
| Express | `express` | 3000 | `node dist/index.js` |
| NestJS | `@nestjs/core` | 3000 | `node dist/main.js` |
| Nuxt | `nuxt` | 3000 | `node .output/server/index.mjs` |
| Koa | `koa` | 3000 | `node dist/app.js` |
| Fastify | `fastify` | 3000 | `node dist/app.js` |

端口优先级：`package.json` 中 `PORT` 环境变量声明 > 框架默认 > 3000。

### Python

| 框架 | 检测关键字 | 端口 | 启动命令 |
|------|------------|------|----------|
| FastAPI | `fastapi` + `uvicorn` | 8000 | `uvicorn app.main:app --host 0.0.0.0 --port 8000` |
| Flask | `flask` | 5000 | `flask run --host 0.0.0.0 --port 5000` |
| Django | `django` | 8000 | `gunicorn project.wsgi:application` |
| Streamlit | `streamlit` | 8501 | `streamlit run app.py --server.port 8501` |

### Java

| 框架 / 打包形态 | 检测关键字 | 运行方式与镜像 | 端口 | 启动命令 |
|------|------------|----------------|------|----------|
| 传统 WAR 包 (Scada-LTS / RuoYi-WAR) | `pom.xml` 中 `<packaging>war</packaging>` 或 `WEB-INF/web.xml` | **必须用 Tomcat**：`tomcat:9.0-jdk8` (Java 8) 或 `tomcat:9.0-jdk11` / `tomcat:10-jdk17` | 8080 | `catalina.sh run`（产物放 `/usr/local/tomcat/webapps/ROOT.war`） |
| Spring Boot (Maven) | `spring-boot-starter` in pom.xml | `eclipse-temurin:<jdk>-jre` | 8080 | `java -jar app.jar` |
| Spring Boot (Gradle) | `build.gradle` / `build.gradle.kts` | `gradle:8-jdk<version>` 构建 + `temurin` 运行 | 8080 | `gradle build --no-daemon -x test` 后 `java -jar app.jar` |
| Quarkus | `quarkus` | `eclipse-temurin:21-jre` | 8080 | `java -jar quarkus-run.jar` |

**Java JDK 版本判定规则**：
- 检查 `pom.xml` 或 `build.gradle` 中的 `<java.version>`, `<maven.compiler.source>`, `sourceCompatibility`：
  - 若为 `1.8` / `8`：编译与运行**必须钉死 JDK 8**（如 `maven:3.8.6-openjdk-8` + `tomcat:9.0-jdk8` 或 `temurin:8-jre`）；
  - 若为 `11`：使用 JDK 11；
  - 若为 `17` / `21`：使用 JDK 17 / 21。

### PHP

| 类型 | 检测关键字 | 运行方式与端口 | 探活特征 |
|------|------------|----------------|----------|
| 传统 CMS / Web 安装向导 (PHP 7.4/8.1) | `install.php` / `install/` / `setup.php` / `discuz` / `zentaopms` | PHP-FPM (7.4/8.1) + Nginx (端口 80) + `chmod -R 777` | 探活返回安装向导页面 (200/302) 即判定成功 |
| 遗留老旧 CMS (PHP 5.6) | 源码含 `mysql_connect(` 或 `mysql_query(`、老版 DedeCMS | `php:5.6-fpm-alpine` + Nginx (端口 80) + `short_open_tag = On` | 探活返回安装向导页面 (200/302) 即判定成功 |
| ThinkPHP | `thinkphp` in composer 或 `think` 可执行文件 | PHP-FPM + Nginx (root: `/public`) | `http://localhost/` |
| Laravel | `laravel/framework` in composer.json | PHP-FPM + Nginx (root: `/public`) | `http://localhost/` |

### .NET

| 框架 | 检测关键字 | 端口 | 启动命令 |
|------|------------|------|----------|
| ASP.NET Core | `Microsoft.NET.Sdk.Web` in .csproj | 8080 | `dotnet App.dll` (需设 `ASPNETCORE_URLS=http://+:8080`) |

### Ruby

| 框架 | 检测关键字 | 端口 | 启动命令 |
|------|------------|------|----------|
| Rails | `rails` in Gemfile | 3000 | `bundle exec rails server -b 0.0.0.0 -p 3000` |
| Sinatra | `sinatra` in Gemfile | 4567 / 8080 | `bundle exec ruby app.rb -o 0.0.0.0` |

### Rust

| 框架 | 检测关键字 | 端口 | 启动命令 |
|------|------------|------|----------|
| Actix-web | `actix-web` in Cargo.toml | 8080 | `./app` |
| Axum | `axum` in Cargo.toml | 8080 | `./app` |
| Rocket | `rocket` in Cargo.toml | 8000 | `./app` |

### Go

| 框架 | 检测关键字 | 端口 | 启动命令 |
|------|------------|------|----------|
| Gin | `github.com/gin-gonic/gin` | 8080 | `./app` |
| Echo | `github.com/labstack/echo` | 8080 | `./app` |
| 标准 net/http | 无框架 | 8080 | `./app` |

## 端口检测补充

除框架默认外，扫描以下来源修正端口：

1. `docker-compose.yml` / `docker-compose.yaml` 已有 `ports` 映射
2. `.env` / `.env.example` 中的 `PORT` / `SERVER_PORT` / `APP_PORT`
3. 源码中 `listen(` / `port =` / `PORT=` 字面量（仅作参考，需人工确认）
4. `README` 中声明的端口

## 版本检测

镜像版本标签来源优先级：

1. `package.json` 的 `version`（nodejs）
2. `pyproject.toml` 的 `[project] version`（python）
3. `pom.xml` 的 `<version>`（java）
4. `Cargo.toml` 的 `version`（rust）
5. `go.mod` 无版本，用 git tag 或默认 `0.1.0`
6. 以上均无，默认 `0.1.0`

## 入口检测

启动命令来源优先级：

1. `package.json` 的 `scripts.start`
2. `Procfile` 的 `web:` 行
3. `Dockerfile` 已有 `CMD` / `ENTRYPOINT`
4. 框架默认启动命令（见上表）
