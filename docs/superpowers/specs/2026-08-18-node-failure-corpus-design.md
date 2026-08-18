# 节点失败语料（已并入对象存储契约）

> 版本: v1.1 · 2026-08-18
> 状态: 已并入 `2026-08-18-object-storage-design.md`
> 定位: 本文不再是 MinIO 布局 SSOT。失败样本的桶/key/客户端以对象存储契约为准；下文只保留行为摘要，避免两份打架。

**SSOT：** [`2026-08-18-object-storage-design.md`](./2026-08-18-object-storage-design.md) §7（kind=`node_run`）以及 §0/§9 中与失败包相关的决策和测试。

变更相对 v1.0 草稿：

- 不再创建 `crucible-node-failure` 桶
- 对象为 `crucible-task` / `node_run/{owner_id}/{task_id}/{run_id}/{node_key}.tar.gz`
- 经 `shared/object_store.py` 写入，不在 task Context 再握 MinIO 客户端

包内布局、错误类、采集时序、索引表 `node_run_failures`、成功/Mock/上传失败策略，均以对象存储契约 §7 为准。
