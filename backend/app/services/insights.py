"""环境历史时间线、占用利用率与分布报表（P0/P1 查询域）。"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    Device, Environment, EnvironmentDevice, Lease, LeaseEnvironment, LeaseResource,
    MaintenanceRecord, PipelineRun, ResourceEvent,
)
from . import WORKSPACE_ID


def environment_timeline(session: Session, env_id: str, limit: int = 100) -> list[dict]:
    """聚合该环境的租约、执行、维护与资源事件，按时间倒序。"""
    items: list[dict] = []
    lease_rows = session.execute(
        select(LeaseEnvironment, Lease).join(Lease, Lease.id == LeaseEnvironment.lease_id)
        .where(LeaseEnvironment.environment_id == env_id)
        .order_by(Lease.start_at.desc()).limit(limit)
    ).all()
    for le, lease in lease_rows:
        items.append({"kind": "lease", "time": lease.start_at.isoformat(),
                      "ref": lease.id, "title": f"借用（{lease.purpose or '未填写用途'}）",
                      "detail": f"{lease.hours} 小时 · {lease.status}", "tone": "blue"})
    run_rows = session.execute(
        select(PipelineRun).where(PipelineRun.environment_id == env_id)
        .order_by(PipelineRun.started_at.desc()).limit(limit)
    ).scalars().all()
    for run in run_rows:
        items.append({"kind": "run", "time": run.started_at.isoformat(), "ref": run.id,
                      "title": f"执行 {run.name}", "detail": f"{run.status} · {run.jenkins_build}",
                      "tone": "teal"})
    record_rows = session.execute(
        select(MaintenanceRecord).where(MaintenanceRecord.environment_ids.contains([env_id]))
        .order_by(MaintenanceRecord.created_at.desc()).limit(limit)
    ).scalars().all()
    for record in record_rows:
        items.append({"kind": "maintenance", "time": record.created_at.isoformat(), "ref": record.id,
                      "title": "维护 / 隔离", "detail": f"{record.reason[:40]} · {record.status}",
                      "tone": "amber"})
    event_rows = session.execute(
        select(ResourceEvent).where(ResourceEvent.entity_type == "environment",
                                    ResourceEvent.entity_id == env_id,
                                    ResourceEvent.action.notin_(["lease_activity"]))
        .order_by(ResourceEvent.occurred_at.desc()).limit(limit)
    ).scalars().all()
    for event in event_rows:
        items.append({"kind": "event", "time": event.occurred_at.isoformat(),
                      "ref": event.action, "title": (event.payload or {}).get("text", event.action),
                      "detail": "", "tone": event.tone})
    items.sort(key=lambda i: i["time"], reverse=True)
    return items[:limit]


def environment_utilization(session: Session, env_id: str, days: int = 7) -> dict:
    """单环境占用历史与利用率（P1）：独占设备时长 ÷ 可服务设备时长。

    口径：按 lease_resources 的 acquired/released（未释放按当前时间截断）计算；
    只统计 kind=manual/run 的真实占用，明确区别于总览页的 7 天固定样本。
    """
    device_ids = list(session.execute(
        select(EnvironmentDevice.device_id).where(
            EnvironmentDevice.workspace_id == WORKSPACE_ID,
            EnvironmentDevice.environment_id == env_id)
    ).scalars().all())
    window_end = datetime.now().astimezone()
    window_start = window_end - timedelta(days=days)
    history: list[dict] = []
    occupied_seconds = 0.0
    if device_ids:
        rows = session.execute(
            select(LeaseResource, Lease).join(Lease, Lease.id == LeaseResource.lease_id).where(
                LeaseResource.workspace_id == WORKSPACE_ID,
                LeaseResource.device_id.in_(device_ids),
                Lease.kind.in_(["manual", "run"]),
                Lease.start_at < window_end,
            )
        ).all()
        for resource, lease in rows:
            start = max(resource.acquired_at, window_start)
            end = resource.released_at or window_end
            end = min(end, window_end)
            if end <= start:
                continue
            seconds = (end - start).total_seconds()
            occupied_seconds += seconds
            history.append({"leaseId": lease.id, "kind": lease.kind,
                            "start": resource.acquired_at.isoformat(),
                            "end": (resource.released_at or window_end).isoformat(),
                            "hours": round(seconds / 3600, 2)})
    serviceable = len(device_ids) * days * 86400
    utilization = round(occupied_seconds / serviceable * 100, 1) if serviceable else 0.0
    return {"envId": env_id, "days": days, "deviceCount": len(device_ids),
            "utilization": utilization, "occupiedHours": round(occupied_seconds / 3600, 1),
            "window": {"start": window_start.isoformat(), "end": window_end.isoformat()},
            "history": sorted(history, key=lambda h: h["start"], reverse=True)[:50]}


def distribution(session: Session) -> dict:
    """资源结构分布（P1）：架构 / 类型 / 位置 / 标签维度统计当前池。"""
    devices = session.execute(
        select(Device).where(Device.workspace_id == WORKSPACE_ID, Device.lifecycle == "active")
    ).scalars().all()
    env_rows = session.execute(
        select(Environment).where(Environment.workspace_id == WORKSPACE_ID,
                                  Environment.lifecycle == "active")
    ).scalars().all()
    locations: dict[str, int] = {}
    for device in devices:
        key = device.location or "未记录"
        locations[key] = locations.get(key, 0) + 1
    tag_count: dict[str, int] = {}
    for env in env_rows:
        for tag in (env.tags or []):
            tag_count[tag] = tag_count.get(tag, 0) + 1
    return {
        "devices": {
            "total": len(devices),
            "byArch": _count(devices, lambda d: d.architecture),
            "byType": _count(devices, lambda d: d.resource_type),
            "byLocation": dict(sorted(locations.items(), key=lambda kv: -kv[1])),
        },
        "environments": {"total": len(env_rows),
                         "tags": dict(sorted(tag_count.items(), key=lambda kv: -kv[1])[:12])},
    }


def status_flow(session: Session, days: int = 7) -> dict:
    """状态流转统计（P2）：窗口内借用/归还/隔离/维护事件计数。"""
    since = datetime.now().astimezone() - timedelta(days=days)
    rows = session.execute(
        select(ResourceEvent.action, func.count()).where(
            ResourceEvent.workspace_id == WORKSPACE_ID,
            ResourceEvent.occurred_at >= since,
            ResourceEvent.action.in_(["batch_borrow_result", "maintenance_start",
                                      "maintenance_verify", "environment_retire",
                                      "environment_restore", "environment_health_check"]),
        ).group_by(ResourceEvent.action)
    ).all()
    return {"days": days, "counts": {action: count for action, count in rows}}


def _count(rows, key) -> dict:
    result: dict[str, int] = {}
    for row in rows:
        k = key(row)
        result[k] = result.get(k, 0) + 1
    return result
