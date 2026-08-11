# 项目类型检测规则

本文件定义如何从项目根目录的文件特征判定语言与框架，用于选择 Dockerfile 模板、默认端口、健康检查方式。`scripts/init_project.py` 按此规则检测。

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

| 框架 | 检测关键字 | 端口 | 启动命令 |
|------|------------|------|----------|
| Spring Boot | `spring-boot-starter` in pom/gradle | 8080 | `java -jar app.jar` |
| Quarkus | `quarkus` | 8080 | `java -jar quarkus-run.jar` |

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

## 检测结果输出

`init_project.py` 检测后输出 JSON 摘要供确认：

```json
{
  "language": "nodejs",
  "framework": "next",
  "version": "1.2.3",
  "port": 3000,
  "start_command": "node server.js",
  "base_image": "node:20-slim",
  "healthcheck": "curl -f http://localhost:3000/health"
}
```

若检测置信度低（如 `other` 或端口不确定），脚本会提示人工确认，不擅自写入配置。
