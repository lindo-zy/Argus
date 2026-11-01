"""演示执行域：已发布配置 + 匹配空闲环境 → running → 终态 → 清理释放。

本轮为模拟执行（无真实 Jenkins 调用），但占用边界与状态机与生产一致；
持久协调器（queue/build 对账、background_tasks）为 Jenkins 接入阶段实现。
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import BusinessError
from ..models import (
    Device, Environment, Lease, LeaseResource, PipelineDefinition, PipelineRun,
    PipelineVersion, User,
)
from ..state import device_occupancy, env_device_ids
from . import WORKSPACE_ID, get_user, next_id, now, record_event
from .leases import _borrow_failure
from .resource_locks import lock_resources
from .maintenance import create_isolation_record


def start_run(session: Session, actor_id: str, payload: dict) -> dict:
    actor = get_user(session, actor_id)
    version = None
    if payload.get("pipelineVersionId"):
        version = session.get(PipelineVersion, payload["pipelineVersionId"])
    elif payload.get("pipelineId"):
        # 兼容按 definition id 启动：解析其已发布版本（多版本取最新）。
        rows = session.execute(
            select(PipelineVersion).where(
                PipelineVersion.definition_id == payload["pipelineId"],
                PipelineVersion.status == "published").order_by(PipelineVersion.version.desc())
        ).scalars().all()
        version = rows[0] if rows else None
    if not version or version.status != "published":
        raise BusinessError("只有已发布配置可以启动任务。")
    definition = session.get(PipelineDefinition, version.definition_id)
    lock_resources(session, [payload.get("envId") or ""])
    env = session.get(Environment, payload.get("envId") or "")
    if not env or env.lifecycle != "active":
        raise BusinessError("环境不存在，请刷新后重试。")
    if env.architecture != definition.architecture:
        raise BusinessError("环境架构与流水线要求不匹配。")
    failure = _borrow_failure(session, env, device_occupancy(session, WORKSPACE_ID))
    if failure is not None:
        raise BusinessError(f"{failure['reason']}。")
    started = now()
    lease = Lease(id=next_id(session, Lease, "LEASE"), workspace_id=WORKSPACE_ID,
                  kind="run", holder_subject=actor.id, purpose=f"{definition.name} 演示执行",
                  status="active", start_at=started,
                  expires_at=started + timedelta(hours=8), hours=8)
    session.add(lease)
    session.flush()
    for device_id in env_device_ids(session, WORKSPACE_ID, env.id):
        device = session.get(Device, device_id)
        session.add(LeaseResource(workspace_id=WORKSPACE_ID, lease_id=lease.id, device_id=device_id,
                                  active=True,
                                  device_snapshot={"name": device.name if device else device_id,
                                                   "holder": actor.name}))
    run = PipelineRun(
        id=next_id(session, PipelineRun, "RUN"), workspace_id=WORKSPACE_ID,
        pipeline_version_id=version.id, lease_id=lease.id, environment_id=env.id,
        name=definition.name, params_snapshot={"IMAGE_REF": "os-24.03-rc2.img", "TEST_SUITE": "smoke"},
        image_ref="os-24.03-rc2.img", os_version_text="OS 24.03 RC2",
        jenkins_job=definition.job_ref or "#mock", jenkins_queue="queue-mock", jenkins_build="#mock",
        status="running", started_by=actor.id, cleanup_state="pending")
    session.add(run)
    session.flush()
    record_event(session, actor_id=actor.id, action="run_activity", entity_type="run",
                 entity_id=run.id, payload={"text": f"{run.id} 已启动 {run.name}"})
    session.flush()
    return run_dict_of(session, run)


def finish_run(session: Session, actor_id: str, run_id: str, success: bool = True) -> dict:
    actor = get_user(session, actor_id)
    run = session.get(PipelineRun, run_id)
    if not run or run.status not in ("running", "reconciling"):
        raise BusinessError("任务当前状态不可结束。")
    run.status = "cleaning"
    run.result = "success" if success else "failed"
    run.progress = 100
    run.passed = run.total if success else run.total - 1
    run.evidence_count = 4
    run.ended_at = now()
    record_event(session, actor_id=actor.id, action="run_activity", entity_type="run",
                 entity_id=run.id, tone="amber", payload={"text": f"{run.id} 已确认终态，等待清理"})
    session.flush()
    return run_dict_of(session, run)


def cleanup_run(session: Session, actor_id: str, run_id: str, healthy: bool) -> dict:
    actor = get_user(session, actor_id)
    run = session.get(PipelineRun, run_id)
    if not run or run.status != "cleaning":
        raise BusinessError("必须确认任务终态后再清理释放。")
    device_ids = []
    if run.lease_id:
        rows = session.execute(select(LeaseResource).where(
            LeaseResource.lease_id == run.lease_id, LeaseResource.active.is_(True))).scalars().all()
        for row in rows:
            row.active = False
            row.released_at = now()
            device_ids.append(row.device_id)
    run.status = run.result or "failed"
    if healthy:
        run.cleanup_state = "done"
        for device_id in device_ids:
            device = session.get(Device, device_id)
            if device:
                device.health_state = "healthy"
                device.health_checked_at = now()
                if device.operational_state == "isolated":
                    device.operational_state = "idle"
    else:
        run.cleanup_state = "isolated"
        for device_id in device_ids:
            device = session.get(Device, device_id)
            if device:
                device.operational_state = "isolated"
                device.health_state = "unhealthy"
        create_isolation_record(session, actor_id=actor.id, environment_ids=[run.environment_id],
                                device_ids=device_ids, reason=f"{run.id} 清理异常，设备已隔离")
    run.evidence_count = 5
    record_event(session, actor_id=actor.id, action="run_activity", entity_type="run",
                 entity_id=run.id, tone="green" if healthy else "red",
                 payload={"text": f"{run.id} 清理{'通过，设备已释放' if healthy else '异常，设备已隔离'}"})
    session.flush()
    return run_dict_of(session, run)


def run_dict_of(session: Session, run: PipelineRun) -> dict:
    starter = session.get(User, run.started_by)
    version = session.get(PipelineVersion, run.pipeline_version_id)
    definition = session.get(PipelineDefinition, version.definition_id) if version else None
    return {
        "id": run.id, "envId": run.environment_id, "name": run.name,
        "status": run.status, "progress": run.progress, "passed": run.passed,
        "total": run.total, "duration": run.duration_minutes, "build": run.jenkins_build,
        "result": run.result, "version": run.os_version_text,
        "user": starter.name if starter else run.started_by,
        "started": run.started_at.isoformat(), "artifact": run.image_ref,
        "config": f"{definition.id if definition else '?'}@{version.version if version else '?'}",
        "evidence": run.evidence_count, "cleanupState": run.cleanup_state,
    }
