# 启动失败的系统化排障与轮询修正

第 6 步。环境跑不通是常态。**系统化定位再改，一次只改一处，改完重试**——不要靠猜乱改多个变量。

## 排障循环

```
起环境 → 失败 → 读日志定位真正报错 → 判断根因层级 → 针对性改一处 → 重试
                     ↑___________________________________________|
```

### 第一步永远是读日志

```bash
docker compose ps                    # 哪个 service 挂了/不健康
docker compose logs <service>        # 看该 service 的报错
docker compose logs --tail=100 -f <service>
docker compose build <service>       # 构建阶段失败时单独看构建输出
```

找到**第一条真正的错误**（往往在报错栈最上方或首次出现 ERROR/FATAL/panic 处），而不是被后续级联错误带偏。

### 判断根因层级

| 层级 | 典型现象 | 改哪里 |
|------|----------|--------|
| 构建期 | `docker build` 失败：依赖装不上、编译错误、COPY 路径错 | Dockerfile |
| 编排期 | service 起不来、互相连不上、启动顺序错、卷挂载错 | docker-compose.yml |
| 应用期 | 容器起了但应用报错退出、健康检查不过 | 应用配置 / 环境变量 |

## 常见问题速查

### 端口冲突（bind: address already in use）

宿主机端口被占。改 compose 里的宿主机侧端口映射，如 `8080:8080` → `18080:8080`，访问地址随之改。查占用：

```bash
netstat -ano | grep :8080     # Windows/Git Bash
```

### 缺环境变量 / 配置

应用报缺少某配置（数据库地址、密钥、JWT secret 等）。在 compose 的 `environment:` 补齐。数据库连接地址用 **service 名**而非 `localhost`（如 `jdbc:mysql://db:3306/...`）。

### 数据库连不上

- 应用比数据库先起：给应用加 `depends_on` + 对 db 做健康检查，应用侧最好有重试。
- 连接串指向了 `localhost`/`127.0.0.1`：容器内应指向 db 的 **service 名**。
- 账号/库未初始化：用官方镜像的 `MYSQL_DATABASE`/`MYSQL_ROOT_PASSWORD`/`POSTGRES_*` 环境变量自动建库建用户；建表脚本挂到 `/docker-entrypoint-initdb.d/`。
- 时区/字符集：MySQL 常需 `--character-set-server=utf8mb4`。

### 构建期依赖装不上 / 慢

- 换国内镜像源（npm/pip/maven/apt）加速。
- Maven/Gradle/npm 用缓存挂载（见 dockerfile-patterns.md）。
- 锁文件缺失导致不可复现：确认 COPY 了 lock 文件。

### 应用起了但访问 404 / 连接被拒

- 应用监听 `127.0.0.1` 而非 `0.0.0.0`：容器内必须监听 `0.0.0.0` 才能被宿主机访问。
- context path：Spring 可能有 `server.servlet.context-path`，访问要带前缀。
- 前端 SPA 路由：nginx 需 `try_files $uri /index.html`。

### 健康检查一直 unhealthy

- 健康检查命令用的端点不存在：换成真实存在的路径（首页 `/` 或实际健康端点）。
- 镜像里没有 `curl`/`wget`：装一个，或改用应用语言自带方式探测。
- `start_period` 太短：Java 等启动慢的应用给足预热时间（如 60s）。

### Maven 镜像构建：`settings file does not exist`

`maven:3.9-*` 镜像的 entrypoint 会在 mvn 运行前把 `/usr/share/maven/ref/` 的内容拷到 `/root/.m2/`，但**如果 Dockerfile 用 `--mount=type=cache,target=/root/.m2`**，cache mount 会**遮盖前序 `COPY xxx /root/.m2/settings.xml` 写入的文件**，导致 mvn 找不到 settings。

**修法**：在同一 RUN 中、cache mount 之后，再拷 settings 并执行 mvn：

```dockerfile
RUN --mount=type=cache,target=/root/.m2 \
    mkdir -p /root/.m2 \
    && cp mvn-settings.xml /root/.m2/settings.xml \
    && mvn -B -s /root/.m2/settings.xml clean package -DskipTests
```

### Maven 镜像构建：`Device or resource busy`

在 `WORKDIR` 上加 `--mount=type=cache,target=/workdir/target` 缓存构建产物，会导致 mvn `clean` 阶段无法删除 `target`（设备忙）。**避免 cache mount 落到 mvn 自身会清理/写入的目录**（如 `target`、`node_modules`），只对纯下载缓存（`/root/.m2`）使用。

### Docker build COPY 静默不生效

偶尔 Docker COPY 日志会因 `progress=plain` 与 BuildKit 缓存而**未输出 COPY 步骤**，但实际文件拷贝到了目标位置（可用 `docker build --progress=plain` 看完整步骤核对）。**关键证据是目标路径在容器内 `ls` 是否存在**。

## 轮询纪律

- **一次只改一个变量**，改完立即重试，确认这一步是否有效。
- 每轮记录：改了什么、结果如何。避免绕圈重复试同一方案。
- 构建缓存导致改动不生效时：`docker compose build --no-cache <service>`。
- 彻底重来：`docker compose down -v` 清掉容器与数据卷再起（注意 `-v` 会删数据，属可逆重建场景，无需额外确认；但若卷中已有用户重要数据则先确认）。
- **设上限**：同一问题连续约 5 次仍无解，停止空转，把卡点、已试方案、关键日志汇报用户请求决策。
