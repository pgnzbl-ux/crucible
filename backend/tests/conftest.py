"""pytest 进程级覆盖：单元测试走 sqlite，不碰 .env 里的 PostgreSQL。

必须在任何 `app` 导入之前设置。环境变量优先于 `.env`，运行时 DATABASE_URL 仍只写在 `.env`。
"""

import os

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
