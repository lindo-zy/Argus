"""后台调度器：周期对账 Jenkins 动态流水线（Job 同步重试 + 运行状态回同步）。

仅进程内尽力而为：每轮为每个运行独立开 Session，单运行失败不影响其余对账；
提交语义仍受 pipeline_executions 约束（至多一次提交，之后只对账）。
"""
from __future__ import annotations

import asyncio

from sqlalchemy import select

from .config import settings


def _tick() -> None:
    from .db import SessionLocal
    from .models import PipelineRun
    from .services.runs import reconcile_run
    from .services.provisioning import resync_outdated_jobs
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
    running_ids = []
    with SessionLocal() as session:
        try:
            running_ids = session.execute(
                select(PipelineRun.id).where(PipelineRun.status.in_(("running", "reconciling")))).scalars().all()
        finally:
            session.close()
    for run_id in running_ids:
        with SessionLocal() as session:
            try:
                run = session.get(PipelineRun, run_id)
                if run is not None:
                    reconcile_run(session, run)
                    session.commit()
            except Exception:
                session.rollback()


async def serve(stop: asyncio.Event) -> None:
    interval = settings().jenkins_poll_interval
    while not stop.is_set():
        try:
            await asyncio.to_thread(_tick)
        except Exception:
            pass  # 对账失败下一轮重试；worker 退出由 stop 控制
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
