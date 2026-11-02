"""状态派生：占用由租约/执行派生，环境状态由设备派生（docs/02、07 口径）。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .errors import OCCUPIED_STATES, STATUS_PRIORITY
from .models import Device, Environment, EnvironmentDevice, Lease, LeaseResource, PipelineRun

# 返回 device_id -> 派生占用状态（仅占用；维护/隔离走 operational_state）。


def device_occupancy(session: Session, workspace_id: str) -> dict[str, str]:
    """派生每台设备的占用状态。

    优先级：待对账/待清理（run 终态确认前）> 待复核（归还 checking）> 执行 > 借用。
    同一设备最多一条 active 占用（数据库唯一索引兜底）。
    """
    rows = session.execute(
        select(LeaseResource.device_id, Lease.kind, Lease.status)
        .join(Lease, Lease.id == LeaseResource.lease_id)
        .where(
            LeaseResource.workspace_id == workspace_id,
            LeaseResource.active.is_(True),
            Lease.status.in_(["active", "checking"]),
        )
    ).all()
    occupancy: dict[str, str] = {}
    run_states = session.execute(
        select(PipelineRun.environment_id, PipelineRun.status).where(
            PipelineRun.workspace_id == workspace_id,
            PipelineRun.status.in_(["running", "reconciling", "cleaning"]),
        )
    ).all()
    env_run_state: dict[str, str] = {}
    for env_id, status in run_states:
        # 同一环境可能存在历史运行；取优先级最高者。
        current = env_run_state.get(env_id)
        if current is None or STATUS_PRIORITY.index(status) < STATUS_PRIORITY.index(current):
            env_run_state[env_id] = status
    for device_id, kind, lease_status in rows:
        if lease_status == "checking":
            occupancy[device_id] = "checking"
        elif kind == "run":
            occupancy[device_id] = "running"
        else:
            occupancy[device_id] = "leased"
    # Run state follows lease device snapshots, including all DAG environments.
    for did, state in session.execute(select(LeaseResource.device_id, PipelineRun.status)
            .join(PipelineRun, PipelineRun.lease_id == LeaseResource.lease_id)
            .where(LeaseResource.active.is_(True), PipelineRun.workspace_id == workspace_id,
                   PipelineRun.status.in_(['reconciling', 'cleaning']))):
        occupancy[did] = state
    if env_run_state:
        # run 的对账/清理覆盖其环境全部设备的派生状态。
        env_devices = session.execute(
            select(EnvironmentDevice.environment_id, EnvironmentDevice.device_id).where(
                EnvironmentDevice.workspace_id == workspace_id,
                EnvironmentDevice.environment_id.in_(env_run_state.keys()),
            )
        ).all()
        for env_id, device_id in env_devices:
            state = env_run_state[env_id]
            if state in ("reconciling", "cleaning"):
                occupancy[device_id] = state
    return occupancy


def device_status(device: Device, occupancy: dict[str, str]) -> str:
    """单台设备最终状态：派生占用优先，其次维护/隔离事实，否则空闲。"""
    if device.id in occupancy:
        return occupancy[device.id]
    if device.operational_state == "isolated":
        return "isolated"
    if device.operational_state == "maintenance":
        return "maintenance"
    return "available"


def environment_status(session: Session, workspace_id: str, env: Environment,
                       occupancy: dict[str, str]) -> str:
    """环境状态 = 全部关联设备状态的最高优先级（docs/02）。"""
    device_rows = session.execute(
        select(Device).join(EnvironmentDevice, EnvironmentDevice.device_id == Device.id).where(
            EnvironmentDevice.workspace_id == workspace_id,
            EnvironmentDevice.environment_id == env.id,
        )
    ).scalars().all()
    states = [device_status(d, occupancy) for d in device_rows]
    for status in STATUS_PRIORITY:
        if status in states:
            return status
    return "available"


def env_device_ids(session: Session, workspace_id: str, env_id: str) -> list[str]:
    rows = session.execute(
        select(EnvironmentDevice.device_id).where(
            EnvironmentDevice.workspace_id == workspace_id,
            EnvironmentDevice.environment_id == env_id,
        )
    ).scalars().all()
    return list(rows)


def is_occupied_status(status: str) -> bool:
    return status in OCCUPIED_STATES


def fmt_health(checked_at: datetime | None) -> str:
    if checked_at is None:
        return "待检查"
    return checked_at.astimezone().strftime("%Y-%m-%d %H:%M")
