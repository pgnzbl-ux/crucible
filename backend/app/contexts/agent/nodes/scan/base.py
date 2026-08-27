"""扫描节点基座 — subprocess 执行 + ScanRun 落库 + SARIF+ 归一化 + 幂等落库。

discovery-spec §6.1：
- 引擎失败 → ScanRun=failed + NodeRun 仍 completed（失败隔离）；
- 引擎不适用/禁用 → ScanRun=skipped + 零 finding + NodeRun completed；
- RawFinding 按 (task_id, fingerprint) upsert，重跑幂等；
- 输出 Handoff：{engine, scan_run_id, status, finding_count}；
- 进度：启动摘要、每 15s 心跳、解析/入库/完成阶段。

适配器只提供：命令构造、超时、输出解析、适用性判断。
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
from collections.abc import Callable
from typing import Any

from ..base import NodeContext, emit_phase

logger = logging.getLogger(__name__)

SCAN_PROGRESS_TICK_SECONDS = 15
_ACTIVE_SCANNER_PROCESS_GROUPS: set[int] = set()


def _scanner_process_group(proc: asyncio.subprocess.Process) -> int | None:
    pid = getattr(proc, "pid", None)
    return pid if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0 else None


def _kill_scanner_process_group(proc: asyncio.subprocess.Process) -> None:
    pgid = _scanner_process_group(proc)
    if pgid is not None:
        try:
            os.killpg(pgid, signal.SIGKILL)
            return
        except ProcessLookupError:
            return
        except OSError:
            logger.warning("终止扫描器进程组失败 pgid=%s，回退终止主进程", pgid, exc_info=True)
    try:
        proc.kill()
    except ProcessLookupError:
        pass


def kill_all_active_scanner_processes() -> int:
    """供 Celery SIGTERM 钩子同步调用，强杀扫描器及其派生进程。"""
    killed = 0
    for pgid in list(_ACTIVE_SCANNER_PROCESS_GROUPS):
        try:
            os.killpg(pgid, signal.SIGKILL)
            killed += 1
        except ProcessLookupError:
            pass
        except OSError:
            logger.warning("SIGTERM 清理扫描器进程组失败 pgid=%s", pgid, exc_info=True)
        finally:
            _ACTIVE_SCANNER_PROCESS_GROUPS.discard(pgid)
    return killed


class EngineScanError(RuntimeError):
    """引擎执行失败(超时/非零退出/不可用)。ScanRun 会标 failed，节点仍完成。"""


class ScanCancelledError(RuntimeError):
    """任务取消导致扫描中止。ScanRun 收敛终态后上抛，由编排器按取消收尾。"""


class EngineScanNode:
    """一引擎一节点：node_key/engine 由适配器类属性声明。"""

    node_key: str = ""
    engine: str = ""

    @property
    def is_ai(self) -> bool:
        return False

    def _resolve_input(self, ctx: NodeContext, node_input):
        from app.contexts.agent.contracts import InputAssembler

        if node_input is not None:
            return node_input
        return InputAssembler.from_previous_outputs(
            self.node_key,
            ctx.previous_outputs,
            host_workdir=ctx.host_workdir,
            source_path=ctx.source_path,
        )

    # ---- 适配器接口 ----

    def enabled(self, settings) -> bool:
        """引擎是否启用(配置开关)。"""
        return True

    def applicable(self, ctx: NodeContext, inp) -> bool:
        """引擎对本次输入是否适用(如 gitleaks 的仓库存在性)。"""
        return True

    def build_command(self, ctx: NodeContext, inp, settings) -> list[str]:
        raise NotImplementedError

    def timeout_seconds(self, settings) -> int:
        raise NotImplementedError

    def parse_output(self, stdout: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    def success_exit_codes(self) -> tuple[int, ...]:
        """视为引擎成功的退出码。semgrep/osv 的 1 = 有 findings，不是崩溃。"""
        return (0,)

    def config_summary(self, ctx: NodeContext, inp, settings) -> dict[str, Any]:
        return {"engine": self.engine}

    def _start_summary_message(self, summary: dict[str, Any], timeout: int) -> str:
        """启动前可读阶段句：semgrep 列规则包；gitleaks 标 git/files；osv 标出网。"""
        configs = summary.get("configs")
        if isinstance(configs, list) and configs:
            packed = ", ".join(str(c) for c in configs)
            return f"规则包 {packed} · 超时上限 {timeout}s"
        mode = summary.get("mode")
        if mode == "git":
            return f"{self.engine} · git 全历史 · 超时上限 {timeout}s"
        if mode == "files":
            return f"{self.engine} · files 模式（无 .git） · 超时上限 {timeout}s"
        if summary.get("network") is True:
            return f"{self.engine} · 整仓 SCA（需出网） · 超时上限 {timeout}s"
        return f"{self.engine} · 超时上限 {timeout}s"

    # ---- 基座执行 ----

    def _repo_root(self, inp, ctx: NodeContext) -> str:
        source = getattr(inp, "source", None)
        return (getattr(source, "project_path", None) if source else None) or ctx.source_path

    async def _run_subprocess(
        self,
        argv: list[str],
        cwd: str,
        settings,
        *,
        on_tick: Callable[[int], None] | None = None,
        stop_check: Callable[[], Any] | None = None,
    ) -> str:
        """执行引擎子进程：超时/输出上限(§8.3)；取消时终止进程。

        二进制不存在/不可执行 → EngineScanError(引擎失败，节点仍 completed)。
        非 success_exit_codes 必须带 stderr，供 NodeRun.error_message 排错。
        on_tick(elapsed_s)：每 SCAN_PROGRESS_TICK_SECONDS 调用一次（进程仍在跑）。
        stop_check()：同一节流周期内 await 的取消探测（返回 True 即杀进程组，
        抛 ScanCancelledError）；缺省不检查。
        """
        max_bytes = settings.scanner_output_max_bytes
        timeout = self.timeout_seconds(settings)
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except (FileNotFoundError, PermissionError) as e:
            raise EngineScanError(f"{self.engine} 不可用: {argv[0]} ({e.__class__.__name__})") from e

        pgid = _scanner_process_group(proc)
        if pgid is not None:
            _ACTIVE_SCANNER_PROCESS_GROUPS.add(pgid)
        started = time.monotonic()
        comm = asyncio.create_task(proc.communicate())
        try:
            while True:
                remaining = timeout - (time.monotonic() - started)
                if remaining <= 0:
                    _kill_scanner_process_group(proc)
                    await proc.wait()
                    comm.cancel()
                    try:
                        await comm
                    except (asyncio.CancelledError, Exception):  # noqa: BLE001
                        pass
                    raise EngineScanError(f"{self.engine} 超时({timeout}s)")
                wait_budget = min(SCAN_PROGRESS_TICK_SECONDS, remaining)
                done, _ = await asyncio.wait({comm}, timeout=wait_budget)
                if done:
                    break
                if on_tick:
                    on_tick(int(time.monotonic() - started))
                if stop_check is not None and await stop_check():
                    _kill_scanner_process_group(proc)
                    await proc.wait()
                    comm.cancel()
                    try:
                        await comm
                    except (asyncio.CancelledError, Exception):  # noqa: BLE001
                        pass
                    raise ScanCancelledError(f"{self.engine} 因任务取消中止")
            stdout, stderr = comm.result()
        except asyncio.CancelledError:
            _kill_scanner_process_group(proc)
            await proc.wait()
            if not comm.done():
                comm.cancel()
                try:
                    await comm
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            raise
        except EngineScanError:
            raise
        except Exception:
            # communicate 已结束后不应再杀；其它异常向上抛
            if not comm.done():
                _kill_scanner_process_group(proc)
                await proc.wait()
            raise
        finally:
            if pgid is not None:
                _ACTIVE_SCANNER_PROCESS_GROUPS.discard(pgid)

        code = proc.returncode or 0
        if code not in self.success_exit_codes():
            err_text = (stderr or b"").decode("utf-8", errors="replace").strip()
            out_text = (stdout or b"").decode("utf-8", errors="replace").strip()
            detail = err_text or out_text or f"退出码 {code}"
            raise EngineScanError(f"{self.engine} 退出码 {code}\n{detail[:8000]}")
        if len(stdout or b"") > max_bytes:
            raise EngineScanError(f"{self.engine} 输出超限({len(stdout)} > {max_bytes})")
        return (stdout or b"").decode("utf-8", errors="replace")

    async def execute(self, ctx: NodeContext, node_input=None) -> dict[str, Any]:
        from app.contexts.discovery.service import DiscoveryService
        from app.core.config import get_settings

        inp = self._resolve_input(ctx, node_input)
        ctx.node_input = inp
        settings = get_settings()
        svc = DiscoveryService(ctx.db_session)
        node_run_id = self._current_node_run_id(ctx)
        summary = self.config_summary(ctx, inp, settings)
        timeout = self.timeout_seconds(settings)
        scan_run = await svc.start_scan_run(
            task_id=ctx.task_id, run_id=ctx.run_id, node_run_id=node_run_id,
            engine=self.engine, config_summary=summary,
        )
        await ctx.db_session.commit()

        def _finish(status: str, count: int = 0, error: str | None = None) -> dict[str, Any]:
            from app.contexts.agent.contracts.outcome import attach_outcome

            payload: dict[str, Any] = {
                "engine": self.engine, "scan_run_id": scan_run.id,
                "status": status, "finding_count": count,
            }
            if error:
                payload["error"] = str(error)
            return attach_outcome(
                payload,
                status=status,
                error=error,
                coverage={
                    "engine": self.engine,
                    "finding_count": count,
                    "scan_status": status,
                },
            )

        if not self.enabled(settings) or not self.applicable(ctx, inp):
            emit_phase(ctx, f"{self.engine} 未启用或不适用，已跳过", phase=self.node_key)
            await svc.finish_scan_run(scan_run, status="skipped")
            await ctx.db_session.commit()
            return _finish("skipped")

        repo_root = self._repo_root(inp, ctx)

        try:
            # 命令构造也在隔离域内：本地规则缺失等构造失败同样走失败隔离，
            # 保证 ScanRun 必达终态、NodeRun 仍 completed（discovery-spec §6.1）
            argv = self.build_command(ctx, inp, settings)
        except EngineScanError as e:
            logger.warning("引擎 %s 命令构造失败(失败隔离，节点仍完成): %s", self.engine, e)
            await svc.finish_scan_run(scan_run, status="failed", error=str(e))
            await ctx.db_session.commit()
            emit_phase(ctx, f"引擎失败：{str(e)[:200]}", phase=self.node_key)
            return _finish("failed", 0, str(e))
        except Exception as e:  # noqa: BLE001 — 构造期意外异常同样不得悬挂 ScanRun
            logger.warning("引擎 %s 命令构造异常(失败隔离): %s", self.engine, e)
            err = f"{self.engine} 命令构造异常: {e}"
            await svc.finish_scan_run(scan_run, status="failed", error=err)
            await ctx.db_session.commit()
            emit_phase(ctx, f"引擎失败：{err[:200]}", phase=self.node_key)
            return _finish("failed", 0, err)

        emit_phase(ctx, self._start_summary_message(summary, timeout), phase=self.node_key)
        emit_phase(ctx, f"启动 {self.engine}", phase=self.node_key)

        def _on_tick(elapsed: int) -> None:
            emit_phase(
                ctx,
                f"扫描进行中…已 {elapsed}s / 上限 {timeout}s",
                phase=self.node_key,
            )

        async def _stop_check() -> bool:
            # 取消轮询与进度心跳同周期：任务取消后扫描器提前终止，
            # 不再跑满整个超时窗口才被丢弃
            if ctx.db_session is None:
                return False
            from ..base import task_run_cancelled

            return await task_run_cancelled(ctx.db_session, ctx.task_id, ctx.run_id)

        try:
            stdout = await self._run_subprocess(
                argv, repo_root, settings, on_tick=_on_tick, stop_check=_stop_check,
            )
            emit_phase(ctx, "解析输出…", phase=self.node_key)
            findings = self.parse_output(stdout)
            if findings:
                emit_phase(ctx, f"入库 {len(findings)} 条…", phase=self.node_key)
            count = await svc.upsert_raw_findings(
                task_id=ctx.task_id, scan_run_id=scan_run.id, findings=findings,
            )
            sarif_key = await svc.archive_sarif(scan_run=scan_run, payload=stdout)
            await svc.finish_scan_run(
                scan_run, status="completed",
                finding_count=len(findings), sarif_key=sarif_key,
            )
            await ctx.db_session.commit()
            emit_phase(ctx, f"完成，命中 {len(findings)} 条", phase=self.node_key)
            return _finish("completed", len(findings))
        except ScanCancelledError as e:
            # 取消不是引擎失败：ScanRun 也要收敛终态（防悬挂的 running 行），
            # 异常上抛后编排器复查库内取消状态，把节点收敛为 cancelled
            emit_phase(ctx, "任务已取消，扫描中止", phase=self.node_key)
            await svc.finish_scan_run(scan_run, status="failed", error=str(e))
            await ctx.db_session.commit()
            raise
        except EngineScanError as e:
            logger.warning("引擎 %s 失败(失败隔离，节点仍完成): %s", self.engine, e)
            await svc.finish_scan_run(scan_run, status="failed", error=str(e))
            await ctx.db_session.commit()
            err = str(e).split("\n", 1)[0]
            emit_phase(ctx, f"引擎失败：{err[:200]}", phase=self.node_key)
            return _finish("failed", 0, str(e))

    def _current_node_run_id(self, ctx: NodeContext) -> str:
        """当前 NodeRun id：编排器经 ctx.node_run_id 注入；缺省时按 key 反查。"""
        nr_id = getattr(ctx, "node_run_id", None)
        if nr_id:
            return nr_id
        return ""
