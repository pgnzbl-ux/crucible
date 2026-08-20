# 数据库迁移（Alembic）

与 `.cursor/rules/db-migrate.mdc` 同源。完整流程见 `.claude/skills/db-migrate/SKILL.md`。

## 提交 migration 前

1. `down_revision` 指向当前 head
2. 更新 `tests/test_schema_baseline.py`（链 + `_alembic_head()`）
3. 相关 `test_*_model.py` 列断言
4. `../.venv/bin/pytest tests/test_schema_baseline.py -q`

## 禁止

- 修改 `c18a0e9b4d21_baseline.py`
- autogenerate 不 review 直接合入
