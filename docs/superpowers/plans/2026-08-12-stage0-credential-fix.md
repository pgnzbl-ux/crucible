# 阶段 0:凭据链路修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 `build_env_from_provider` 不解密导致容器拿到空 key、所有任务 401 必败的 bug;修复 `executor.py` 读 stderr 时容器已删的诊断信息丢失。

**Architecture:** 凭据存储恢复 Fernet 加密语义(撤销 working copy 把加密改明文的未提交改动);`agent_runner.run_with_streaming` 在删容器前抓 stderr 存入 summary,让 executor 能取到真实错误。

**Tech Stack:** Python 3.11 / SQLAlchemy 2.0 Async / cryptography.Fernet / Docker SDK

## Global Constraints

- 提交信息用简体中文主体 + 英文 type 前缀(项目 CLAUDE.md 约定)
- Windows 开发用 Git Bash,路径用正斜杠
- 不可逆操作(git checkout 还原文件)执行前确认改动已备份或确认无价值
- 凭据相关日志一律掩码(`redact_env_for_log`)

---

## 背景与根因(给实施者)

`backend/app/contexts/settings/service.py` 等 5 个文件有**未提交的 working copy 改动**,把原本的 Fernet 加密链路改成了明文存取:

- `create_provider`:`encrypt_secret(request.api_key)` → `request.api_key`
- `build_env_from_provider`:`decrypt_secret(provider.api_key_encrypted)` → `provider.api_key_encrypted`(直接用)
- `test_connection` / `to_response` / `create_credential` / `_credential_to_response` 同样去掉了加解密

但 DB 里现存的 provider 记录 `api_key_encrypted` 字段是 Fernet 密文(`gAAAAA...`,用历史 AUTH_SECRET 加密),且当前 AUTH_SECRET 派生的 key 解不开(InvalidToken)。当前明文代码直接把这个密文当 API key 注入容器 → DeepSeek 401 → 任务必败。

**修复策略(方案 A 恢复加密):** 撤销这 5 个文件的 working copy 改动,恢复 Fernet 加密链路。DB 旧密文因 key 不匹配无法解密 → 用户在 web 重新输入真实 key(按恢复后的加密逻辑落库)。

次生 bug:`executor.py:154` 在容器失败后去读 stderr,但容器已在 `agent_runner.py:302 run_with_streaming` 的 finally 里被 `stop_and_remove` 删掉 → `containers.get` 抛 NotFound 被吞 → stderr_tail 永远空 → error_message 只剩"非0退出"。

---

## File Structure

| 文件 | 改动 | 责任 |
|---|---|---|
| `backend/app/contexts/settings/{models,schemas,service,seed}.py` | 撤销 working copy | 恢复 Fernet 加密存取 |
| `backend/app/core/credential_proxy.py` | 撤销 working copy | 恢复 decrypt_secret |
| `backend/app/core/agent_runner.py` | 修改 `run_with_streaming` | 删容器前抓 stderr 存 summary |
| `backend/app/contexts/agent/executor.py` | 修改 stderr 取法 | 从 summary 取 stderr_tail |
| `backend/tests/test_credential_crypto.py` | 新建 | 验证加密存取一致性 |
| `backend/tests/test_agent_runner_stderr.py` | 新建 | 验证失败时 stderr 可获取 |

---

### Task 1: 撤销凭据链路明文改动,恢复 Fernet 加密

**Files:**
- Modify(还原): `backend/app/contexts/settings/service.py`
- Modify(还原): `backend/app/contexts/settings/models.py`
- Modify(还原): `backend/app/contexts/settings/schemas.py`
- Modify(还原): `backend/app/contexts/settings/seed.py`
- Modify(还原): `backend/app/core/credential_proxy.py`
- Test: `backend/tests/test_credential_crypto.py`

**Interfaces:**
- Produces: `SettingsService.build_env_from_provider(provider) -> dict` 恢复为返回**解密后**的明文 env;`create_provider`/`update_provider`/`create_credential`/`update_credential` 恢复为 `encrypt_secret` 落库;`to_response`/`test_connection`/`_credential_to_response` 恢复 `decrypt_secret`。

- [ ] **Step 1: 确认要撤销的改动范围**

Run: `cd backend && git diff HEAD --stat -- app/contexts/settings/ app/core/credential_proxy.py`
Expected: 显示 5 个文件改动(models 8 行 / schemas 8 行 / seed 3 行 / service 27 行 / credential_proxy 5 行)。

- [ ] **Step 2: 撤销这 5 个文件到 HEAD**

Run:
```bash
cd backend
git checkout HEAD -- app/contexts/settings/service.py app/contexts/settings/models.py app/contexts/settings/schemas.py app/contexts/settings/seed.py app/core/credential_proxy.py
```
Expected: 无输出。`git status` 显示这 5 个文件不再 modified。

- [ ] **Step 3: 验证还原后代码含 decrypt_secret/encrypt_secret**

Run: `cd backend && grep -nE "encrypt_secret|decrypt_secret" app/contexts/settings/service.py app/core/credential_proxy.py app/contexts/settings/seed.py`
Expected: service.py 出现 `from app.core.crypto import decrypt_secret, encrypt_secret, mask_secret` + 5 处加解密调用;credential_proxy.py 出现 `from app.core.crypto import decrypt_secret`;seed.py 出现 `encrypt_secret(settings.llm_api_key)`。

- [ ] **Step 4: 写失败测试 — 加密存取一致性**

Create `backend/tests/test_credential_crypto.py`:

```python
"""凭据加密链路一致性测试。

验证 Provider/Credential 的 api_key 落库前 Fernet 加密、
读取(build_env / test_connection / to_response)时解密。
"""
import sqlite3
import asyncio
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


@pytest.mark.asyncio
async def test_build_env_decrypts_provider_key(tmp_path, monkeypatch):
    """build_env_from_provider 必须返回解密后的明文 key,不是密文。"""
    from app.core import crypto
    from app.contexts.settings.models import LlmProvider
    from app.contexts.settings.service import SettingsService, SettingsRepository
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

    # 用一个固定的真实 key,加密后塞进 provider(模拟 DB 存的密文)
    real_key = "sk-test-1234567890abcdef"
    encrypted = crypto.encrypt_secret(real_key)
    assert encrypted != real_key, "加密后应与明文不同"
    assert encrypted.startswith("gAAAAA"), "Fernet 密文特征"

    provider = LlmProvider(
        id="p1", name="test", provider_type="deepseek",
        base_url="https://api.deepseek.com/anthropic",
        api_key_encrypted=encrypted,
        model="deepseek-v4-flash", timeout_ms=600000,
        enabled=True, is_default=True,
    )

    # build_env_from_provider 是同步方法,直接调
    svc = SettingsService.__new__(SettingsService)  # 不走 repo 初始化
    env = svc.build_env_from_provider(provider)

    # 核心断言:注入容器的 key 是明文,不是密文
    assert env["ANTHROPIC_API_KEY"] == real_key, "build_env 应解密,注入明文"
    assert env["ANTHROPIC_AUTH_TOKEN"] == real_key
    assert env["ANTHROPIC_API_KEY"] != encrypted, "绝不能注入密文"
    assert env["ANTHROPIC_BASE_URL"] == "https://api.deepseek.com/anthropic"
    assert env["ANTHROPIC_MODEL"] == "deepseek-v4-flash"


def test_encrypt_decrypt_roundtrip():
    """加密→解密往返一致。"""
    from app.core.crypto import encrypt_secret, decrypt_secret
    for plaintext in ["sk-abc", "short", "a" * 100, "中文密钥测试"]:
        enc = encrypt_secret(plaintext)
        assert enc.startswith("gAAAAA")
        assert decrypt_secret(enc) == plaintext


def test_decrypt_invalid_returns_empty():
    """解密失败(key 不匹配)返回空串,不抛异常。"""
    from app.core.crypto import decrypt_secret
    # 伪造的无效 token
    assert decrypt_secret("gAAAAAinvalid") == ""
    assert decrypt_secret("") == ""
```

- [ ] **Step 5: 跑测试验证失败(还原后应通过,先确认测试本身能跑)**

Run: `cd backend && python -m pytest tests/test_credential_crypto.py -v`
Expected: 3 个测试 PASS(还原后代码已含 decrypt_secret)。如果 FAIL,说明还原不完整,回 Step 2 检查。

- [ ] **Step 6: Commit**

```bash
cd backend
git add app/contexts/settings/service.py app/contexts/settings/models.py app/contexts/settings/schemas.py app/contexts/settings/seed.py app/core/credential_proxy.py tests/test_credential_crypto.py
git commit -m "$(cat <<'EOF'
fix(credential): 恢复 Fernet 加密链路,修复容器拿到空 key 导致 401

撤销 working copy 把加密改明文的未提交改动:
build_env_from_provider 恢复 decrypt_secret,确保注入容器的
是解密后的明文 key 而非 DB 密文。补加密存取一致性测试。

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: 清理 DB 不可解密的旧密文记录

**Files:**
- 无代码改动,纯数据修复脚本

**说明:** 还原加密代码后,DB 里现存的 `gAAAAA...` 密文用当前 AUTH_SECRET 仍解不开(历史 key 加密的)。这个记录会让用户困惑(web 显示有 provider 但任务失败)。策略:删除该坏记录,让用户在 web 重新输入真实 key(走恢复后的加密 create_provider)。

- [ ] **Step 1: 确认 DB 里待清理的记录**

Run:
```bash
cd backend
python -c "
import sqlite3
con = sqlite3.connect('crucible.db'); con.row_factory = sqlite3.Row
for r in con.execute('SELECT id, name, substr(api_key_encrypted,1,12) as prefix, is_default FROM llm_providers'):
    print(dict(r))
"
```
Expected: 显示至少 1 条 `prefix=gAAAAA...`(坏密文)。

- [ ] **Step 2: 确认坏记录解不开(二次核实)**

Run:
```bash
cd backend
python -c "
import sqlite3, sys
sys.path.insert(0,'.')
from app.core.crypto import decrypt_secret
con = sqlite3.connect('crucible.db'); con.row_factory = sqlite3.Row
for r in con.execute('SELECT id, api_key_encrypted FROM llm_providers'):
    d = decrypt_secret(r['api_key_encrypted'])
    print(r['id'][:8], 'decrypt_len=', len(d), 'decryptable=', bool(d))
"
```
Expected: `decrypt_len=0 decryptable=False`。如果是 True,说明密文其实可解,跳过 Task 2,直接让用户重填也行。

- [ ] **Step 3: 删除不可解密的坏记录**

Run:
```bash
cd backend
python -c "
import sqlite3
con = sqlite3.connect('crucible.db')
con.execute('DELETE FROM llm_providers WHERE is_default=1')
con.commit()
print('remaining providers:', con.execute('SELECT count(*) FROM llm_providers').fetchone()[0])
"
```
Expected: `remaining providers: 0`。

- [ ] **Step 4: 重启后端 + worker,让用户在 web 重新填 key**

向用户说明(不是代码步骤,是交付说明):
> 凭据链路已修复为 Fernet 加密。已清除 DB 里旧的不可解密 provider 记录。请在 web 设置页重新添加 DeepSeek provider(填真实 API Key),创建后它会按加密逻辑落库,任务即可正常跑。

- [ ] **Step 5: (无需 commit,数据修复不进版本控制)**

---

### Task 3: 修复 agent_runner stderr 诊断信息丢失

**Files:**
- Modify: `backend/app/core/agent_runner.py:237-305`(`run_with_streaming`)
- Modify: `backend/app/contexts/agent/executor.py:146-182`
- Test: `backend/tests/test_agent_runner_stderr.py`

**Interfaces:**
- `AgentRunnerManager.run_with_streaming(spec, on_event) -> tuple[int, dict]` 的 summary dict **新增 `stderr_tail: str` 字段**(容器失败时,删容器前抓取的 stderr 最后 50 行)。
- `ClaudeSdkExecutor.run` 从 `summary["stderr_tail"]` 取错误诊断,不再 `containers.get(container_id)`(那时容器已删)。

**根因:** `agent_runner.py:300-304` finally 块先 `stop_and_remove`,然后 executor.py:154 才去 `containers.get(container_id).logs(stderr)` → 容器已删 → NotFound 被吞 → stderr_tail 空。

- [ ] **Step 1: 读现有 run_with_streaming 确认改动点**

Run: `cd backend && sed -n '237,305p' app/core/agent_runner.py`
确认 `container.wait()` 后、`finally` 前是抓 stderr 的位置。

- [ ] **Step 2: 写失败测试 — 失败容器的 stderr 进 summary**

Create `backend/tests/test_agent_runner_stderr.py`:

```python
"""验证 run_with_streaming 在容器失败时把 stderr 存入 summary。

回归 bug:容器在 finally 里被 stop_and_remove 删除后,
executor 再去 containers.get 取不到 stderr,导致 error_message
只剩"非0退出"。修复后 summary 应含 stderr_tail。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import MagicMock, patch
from app.core.agent_runner import AgentRunnerManager, AgentRunnerSpec


def test_failed_container_stderr_in_summary():
    """容器非 0 退出时,summary 含 stderr_tail(从 container.logs 取)。"""
    fake_container = MagicMock()
    # 容器失败,stderr 有内容
    fake_container.logs.return_value = b"some error on stderr\nFATAL: bad key\n"
    wait_result = {"StatusCode": 1}
    fake_container.wait.return_value = wait_result
    fake_container.attrs = {"State": {"OOMKilled": False}}
    fake_container.id = "cid123"
    fake_container.reload = MagicMock()

    fake_runner = MagicMock()
    fake_runner.container = fake_container
    fake_runner.id = "cid123"
    fake_runner.name = "test-runner"
    fake_runner.stop_and_remove = MagicMock()

    mgr = AgentRunnerManager.__new__(AgentRunnerManager)
    mgr._client = MagicMock()

    with patch.object(mgr, "create", return_value=fake_runner):
        spec = AgentRunnerSpec(host_workdir="/tmp/x", env={})
        events = []
        exit_code, summary = mgr.run_with_streaming(spec, events.append)

    assert exit_code == 1
    assert "stderr_tail" in summary, "summary 必须含 stderr_tail"
    assert "FATAL: bad key" in summary["stderr_tail"]
    assert summary["container_id"] == "cid123"
```

- [ ] **Step 3: 跑测试验证失败**

Run: `cd backend && python -m pytest tests/test_agent_runner_stderr.py -v`
Expected: FAIL with `KeyError: 'stderr_tail'`(当前 summary 没这字段)。

- [ ] **Step 4: 改 run_with_streaming — 删容器前抓 stderr**

Modify `backend/app/core/agent_runner.py`,在 `run_with_streaming` 内、`return` 前、`finally` 前(约 L287-293 之间,`summary = {...}` 构造处)加 stderr 抓取。

定位现有 summary 构造(约 L287):
```python
            summary = {
                "container_id": runner.id,
                "container_name": runner.name,
                "exit_code": exit_code,
                "oom_killed": oom_killed,
            }
            return exit_code, summary
```

改为:
```python
            # 失败时(非 0 且非 OOM),删容器前抓 stderr,供 executor 诊断
            stderr_tail = ""
            if exit_code != 0 and not oom_killed:
                try:
                    raw = runner.container.logs(tail=50, stdout=False, stderr=True)
                    if isinstance(raw, bytes):
                        stderr_tail = raw.decode("utf-8", errors="replace")
                    else:
                        stderr_tail = str(raw)
                except Exception:
                    pass

            summary = {
                "container_id": runner.id,
                "container_name": runner.name,
                "exit_code": exit_code,
                "oom_killed": oom_killed,
                "stderr_tail": stderr_tail,
            }
            return exit_code, summary
```

- [ ] **Step 5: 跑测试验证通过**

Run: `cd backend && python -m pytest tests/test_agent_runner_stderr.py -v`
Expected: PASS。

- [ ] **Step 6: 改 executor — 从 summary 取 stderr_tail**

Modify `backend/app/contexts/agent/executor.py`,把 L146-160 的 stderr 取法从 `containers.get` 改为从 summary 取。

现有代码(L146-160):
```python
        if exit_code == 137:
            result.conclusion = "cancelled"
            result.error_message = "agent_runner_killed"
        elif exit_code != 0 and exit_code != 2:
            # 2 = 基础设施错误（OCI / 镜像）；其它非 0 = 业务失败
            stderr_tail = ""
            try:
                if result.container_id:
                    stderr_tail = agent_runner_manager._client.containers.get(
                        result.container_id
                    ).logs(tail=50, stdout=False, stderr=True).decode("utf-8", errors="replace")
            except Exception:
                pass
            result.conclusion = "failed"
            result.error_message = (stderr_tail or result.error_message or "agent_runner 非 0 退出")[:500]
```

改为(注意 `summary` 变量在 L131 已绑定 `exit_code, summary = agent_runner_manager.run_with_streaming(...)`):
```python
        if exit_code == 137:
            result.conclusion = "cancelled"
            result.error_message = "agent_runner_killed"
        elif exit_code != 0 and exit_code != 2:
            # 2 = 基础设施错误（OCI / 镜像）；其它非 0 = 业务失败
            # stderr_tail 已由 run_with_streaming 在删容器前抓好(避免容器已删取不到)
            stderr_tail = summary.get("stderr_tail", "") if summary else ""
            result.conclusion = "failed"
            result.error_message = (stderr_tail or result.error_message or "agent_runner 非 0 退出")[:500]
```

- [ ] **Step 7: 确认 summary 变量在 executor 作用域可见**

Run: `cd backend && grep -n "summary" app/contexts/agent/executor.py`
Expected: L131 附近 `exit_code, summary = agent_runner_manager.run_with_streaming(...)` — summary 在 run 方法作用域内可见(L146 用到的地方同作用域)。

- [ ] **Step 8: Commit**

```bash
cd backend
git add app/core/agent_runner.py app/contexts/agent/executor.py tests/test_agent_runner_stderr.py
git commit -m "$(cat <<'EOF'
fix(agent): 失败容器 stderr 在删除前抓取,修复诊断信息丢失

run_with_streaming 在 stop_and_remove 之前抓 stderr 存入 summary,
executor 改从 summary 取 stderr_tail。此前容器在 finally 里先被删,
executor 再 containers.get 取不到,错误消息只剩"非0退出"。

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: 端到端冒烟验证凭据链路通

**Files:**
- 无新文件,手动验证步骤

**说明:** Task 1-3 合并后的端到端验证。需要用户在 web 重填 key 后进行(依赖 Task 2 Step 4 的用户动作)。

- [ ] **Step 1: 确认后端 + worker 在跑(还原代码后需重启)**

Run: `powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { \$_.CommandLine -match 'celery|run_worker|uvicorn' } | Select-Object ProcessId, @{N='Cmd';E={\$_.CommandLine.Substring(0,80)}}"`
Expected: 看到 run_worker.py + uvicorn 进程。如果代码还原后没重启,提示用户重启(尤其 worker,无 --reload)。

- [ ] **Step 2: 提示用户在 web 设置页添加 DeepSeek provider**

向用户交付:
> 请在 web 设置页添加 DeepSeek provider(Base URL `https://api.deepseek.com/anthropic`,填真实 API Key,模型 `deepseek-v4-flash`),设为默认。这一步必须,因为 DB 旧坏记录已清。

- [ ] **Step 3: 提示用户下个测试任务,观察是否还 401**

向用户交付:
> 添加 provider 后,创建一个测试任务(任意 git 项目 + 漏洞描述),观察任务详情的事件流。如果凭据链路修好了,应该能看到 agent-runner 容器真正调用 SDK(出现 agent.message / tool.call 事件),而不是 10 秒就失败。

- [ ] **Step 4: 如果仍失败,用修好的 stderr_tail 看真实错误**

Run(任务失败后):
```bash
cd backend
python -c "
import sqlite3
con = sqlite3.connect('crucible.db'); con.row_factory = sqlite3.Row
r = con.execute('SELECT error_message FROM task_runs ORDER BY started_at DESC LIMIT 1').fetchone()
print(r['error_message'] if r else 'no runs')
"
```
Expected: error_message 现在含真实 stderr(不再是"非0退出"),据此进一步诊断。

- [ ] **Step 5: (冒烟通过即阶段 0 完成,无需 commit)**

---

## Self-Review 结果

**Spec coverage(§8 凭据修复):**
- §8.2 方案 A 恢复加密 → Task 1 ✓
- §8.2 "DB 旧密文让用户重填" → Task 2 ✓
- §8.2 次生 bug "stderr 读时容器已删" → Task 3 ✓
- §8.3 端到端验证 → Task 4 ✓

**Placeholder scan:** 无 TBD/TODO;所有代码步骤含完整代码块。

**Type consistency:** `summary` dict 新增 `stderr_tail: str`,executor 用 `summary.get("stderr_tail", "")` 一致;`build_env_from_provider` 还原为解密,测试断言 `env["ANTHROPIC_API_KEY"] == real_key`。

**已知风险:**
- Task 1 的 `git checkout HEAD -- ...` 会丢弃 working copy 改动。这些改动全是"加密→明文"回归(用户未提交,且与 security.md §3 红线冲突),丢弃是预期行为。但实施前应 `git diff` 再看一眼确认无其他有价值改动(Step 1 已覆盖)。
- Task 1 测试用 `SettingsService.__new__` 跳过 repo 初始化(因为 build_env_from_provider 不依赖 repo),这是合理的单元测试隔离。
- Task 4 依赖用户在 web 操作,不能自动化,作为交付 gate。
