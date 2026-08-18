---
name: db-migrate
description: Alembic 迁移生成与执行
---

# Database Migrate

当前有一条 **基线** `alembic/versions/c18a0e9b4d21_baseline.py`（`upgrade()` = 当前 ORM `metadata.create_all`），以及后续增量 revision。开发启动时 `init_db()` 走同一套 metadata 并补缺失索引，再 stamp `alembic_version` 到 head。库地址只从 `.env` 的 `DATABASE_URL` 读（`alembic/env.py` 注入，不要把真实 URL 写进 `alembic.ini` / `config.py`）。

## 1. 空库部署

```bash
cd backend
alembic upgrade head
```

开发空库在 API 启动时按当前模型建表，也可：

```bash
cd backend
alembic upgrade head
```

已有库（表已在）不要对基线再 `upgrade` 指望改列；启动时 `init_db()` 会按 metadata 补缺失索引（含 unique 标志不一致时重建）并 stamp 到当前 head。从旧多版本链过来的库 stamp 即可。增量约束变更优先跑 `alembic upgrade head`。

## 2. 模型变更后再出增量

改 Context `models.py` 后：

```bash
cd backend
alembic revision --autogenerate -m "add xxx"
```

- **必须**人工 review autogenerate 输出，alembic 不能识别所有变更（如 enum rename / column type）
- 跨 Context 新表必须放进对应 Context 的 `models.py`（worker import 注册 metadata，参考 `backend.md` §1）
- 基线文件不要改历史、不要再叠旧链

## 3. 回滚

```bash
alembic downgrade -1     # 回一版（目前基线的 -1 是 drop_all，会删全部表）
alembic downgrade base   # 全回
```

生产回滚需用户确认（参考 `git-workflow.md` §5 不可逆操作）。

## 4. 注意事项

- 不要在迁移里塞业务代码 / 数据回填（用单独脚本）
- 不要 drop 列只做 `server_default` 移除，迁移历史要可回滚
