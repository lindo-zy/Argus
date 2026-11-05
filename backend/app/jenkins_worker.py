"""嵌入式 Jenkins 后台调度器；可多 API 进程并行运行，互不冲突。

两条循环共用一个 worker：
- orchestration.tick：DAG 节点编排的持久推进（BackgroundTask 认领/围栏，1s 节拍）；
- 动态流水线对账（runs.reconcile_run + provisioning.resync_outdated_jobs）：
  按 ARGUS_TOKEN 回同步 queue→build→终态并重试 Job 同步（ARGUS_JENKINS_POLL_INTERVAL 节拍）。
"""
import asyncio
import logging

from sqlalchemy import select

from .config import settings

logger = logging.getLogger(__name__)


def _dynamic_tick() -> None:
    """动态流水线对账一轮：每运行独立 Session 独立提交，单点失败不拖累其余。"""
    from .db import SessionLocal
    from .models import PipelineRun
    from .services.provisioning import resync_outdated_jobs
    from .services.runs import reconcile_run
    from .services.users import resolve_by_token

    # worker 身份：演示级回退到引导管理员 token；事件归属仍记录真实 run 发起人。
    with SessionLocal() as session:
        try:
            actor = resolve_by_token(session, settings().bootstrap_admin_token)
            actor_id = actor.id if actor else ""
        except Exception:
            actor_id = ""
    if not actor_id:
        return
    with SessionLocal() as session:
        try:
            resync_outdated_jobs(session, actor_id)
            session.commit()
        except Exception:
            session.rollback()
    with SessionLocal() as session:
        try:
            running_ids = session.execute(
                select(PipelineRun.id).where(PipelineRun.status.in_(("running", "reconciling")))).scalars().all()
        except Exception:
            session.rollback()
            return
    for run_id in running_ids:
        with SessionLocal() as session:
            try:
                run = session.get(PipelineRun, run_id)
                if run is not None:
                    reconcile_run(session, run)
                    session.commit()
            except Exception:
                session.rollback()


async def serve(stop: asyncio.Event):
    from .db import SessionLocal
    from .services.orchestration import tick

    interval = max(1.0, settings().jenkins_poll_interval)
    next_dynamic = 0.0
    while not stop.is_set():
        try:
            await asyncio.to_thread(tick, SessionLocal)
        except Exception:
            # Don't log DB URLs/HTTP authentication. Persisted claims expire for recovery.
            logger.error('Jenkins coordinator iteration failed; durable intent retained for recovery')
        loop = asyncio.get_running_loop()
        if loop.time() >= next_dynamic:
            next_dynamic = loop.time() + interval
            try:
                await asyncio.to_thread(_dynamic_tick)
            except Exception:
                logger.error('Dynamic pipeline reconcile iteration failed; will retry')
        try:
            await asyncio.wait_for(stop.wait(), timeout=1)
        except TimeoutError:
            pass
