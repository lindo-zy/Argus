"""借用领域服务：批量部分成功、设备互斥、幂等（docs/02、03、07）。

一致性口径（生产）：
- 服务端重查拓扑与设备状态；固定顺序锁定候选环境与设备（短事务）。
- 逐环境检查：冲突项记 skipped，不留部分占用；同批已获得的共享设备同一租约持有。
- 首个成功项才创建租约；全部失败只记结果事件。
- (workspace, actor, request_key) 唯一约束实现幂等：重放返回相同结果；同键不同参数报冲突。
- 数据库 active 设备唯一索引兜底互斥，并发冲突按业务跳过处理。
"""
from __future__ import annotations

import hashlib
import json
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..errors import OP_IDLE, BusinessError
from ..models import Device, Environment, EnvironmentDevice, Lease, LeaseEnvironment, LeaseResource, ResourceEvent
from ..state import device_occupancy, device_status, env_device_ids
from . import WORKSPACE_ID, get_user, next_id, now, record_event

VALID_HOURS = (2, 4, 8, 24, 48)

REASONS = {
    "leased": "环境或共享设备已被借用",
    "running": "环境或共享设备正在执行任务",
    "maintenance": "环境或共享设备正在维护",
    "isolated": "关联设备已隔离，等待健康复核",
    "reconciling": "关联设备有待对账任务",
    "cleaning": "关联设备正在等待清理",
    "checking": "关联设备正在等待归还复核",
}


def _hash_params(actor_id: str, env_ids: list[str], hours: int, purpose: str) -> str:
    raw = json.dumps([actor_id, env_ids, hours, purpose], ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _derive_key(actor_id: str, env_ids: list[str], hours: int, purpose: str) -> str:
    return f"borrow-{_hash_params(actor_id, env_ids, hours, purpose)}"


def _borrow_failure(session: Session, env: Environment | None,
                    occupancy: dict[str, str]) -> dict | None:
    """等价 mock borrowFailure：返回 {code, reason} 或 None（可借）。"""
    if env is None or env.lifecycle != "active":
        return {"code": "not_found" if env is None else "retired",
                "reason": "环境不存在或已被移除" if env is None else "环境已移出资源池"}
    current_ids = env_device_ids(session, WORKSPACE_ID, env.id)
    device_rows = session.execute(
        select(Device).join(EnvironmentDevice, EnvironmentDevice.device_id == Device.id).where(
            EnvironmentDevice.environment_id == env.id)
    ).scalars().all()
    active_devices = [d for d in device_rows if d.lifecycle == "active"]
    if not current_ids or not active_devices:
        return {"code": "invalid_configuration", "reason": "环境尚未关联设备"}
    if any(d.id not in current_ids for d in device_rows) or len(active_devices) != len(current_ids):
        return {"code": "invalid_configuration", "reason": "关联设备不存在或已移出资源池"}
    for device in active_devices:
        status = device_status(device, occupancy)
        if status != "available":
            return {"code": status, "reason": REASONS.get(status, "环境当前不可借用")}
    return None


def borrow(session: Session, actor_id: str, payload: dict,
           request_key: str = "") -> dict:
    """批量直接借用：逐项成功/跳过；成功环境一张租约、设备去重。"""
    actor = get_user(session, actor_id)
    hours = payload.get("hours")
    purpose_raw = payload.get("purpose")
    env_ids_raw = payload.get("envIds", payload.get("envId"))
    if hours is None or int(hours) not in VALID_HOURS:
        raise BusinessError("请选择有效的借用时长。")
    hours = int(hours)
    if not isinstance(purpose_raw, str) or not purpose_raw.strip() or len(purpose_raw.strip()) > 200:
        raise BusinessError("请填写 1–200 字的借用用途。")
    purpose = purpose_raw.strip()
    if env_ids_raw is None:
        requested: list[str] = []
    elif isinstance(env_ids_raw, list):
        requested = list(env_ids_raw)
    else:
        requested = [env_ids_raw]
    if not requested or any(not isinstance(i, str) or not i.strip() for i in requested):
        raise BusinessError("请至少选择一个有效环境。")
    requested = list(dict.fromkeys(requested))

    request_hash = _hash_params(actor.id, requested, hours, purpose)
    request_key = request_key or _derive_key(actor.id, requested, hours, purpose)

    # 幂等重放：同键同参返回既有结果；同键异参视为请求冲突。
    prior = session.execute(
        select(ResourceEvent).where(
            ResourceEvent.workspace_id == WORKSPACE_ID,
            ResourceEvent.actor_subject == actor.id,
            ResourceEvent.request_key == request_key,
            ResourceEvent.action == "batch_borrow_result",
        )
    ).scalar_one_or_none()
    if prior is not None:
        if prior.request_hash != request_hash:
            raise BusinessError("幂等键已用于不同参数的借用请求，请更换请求键。", code="request_conflict")
        return prior.result_snapshot

    started = now()
    outcomes: list[dict] = []
    selected_envs: list[Environment] = []
    lease: Lease | None = None

    # 固定顺序锁定候选环境，再锁定设备（与拓扑编辑共享锁顺序）。
    env_rows = session.execute(
        select(Environment).where(Environment.id.in_(requested)).order_by(Environment.id)
        .with_for_update()
    ).scalars().all()
    env_map = {e.id: e for e in env_rows}
    all_device_ids = sorted({d for e in env_rows
                             for d in session.execute(
                                 select(EnvironmentDevice.device_id).where(
                                     EnvironmentDevice.workspace_id == WORKSPACE_ID,
                                     EnvironmentDevice.environment_id == e.id)).scalars()})
    locked_devices = {d.id: d for d in session.execute(
        select(Device).where(Device.id.in_(all_device_ids)).order_by(Device.id).with_for_update()
    ).scalars().all()}
    occupancy = device_occupancy(session, WORKSPACE_ID)

    claimed: dict[str, Device] = {}  # 本批已获得的共享设备（同一租约去重持有）

    for env_id in requested:
        env = env_map.get(env_id)
        # 同批已获得的共享设备视为可复用（docs/02：同批 A、B 共用 D 一起成功，只占用一次）。
        effective = {k: v for k, v in occupancy.items() if k not in claimed}
        failure = _borrow_failure(session, env, effective)
        if failure is None and env is not None:
            # 逐环境 SAVEPOINT：唯一索引冲突等并发问题只影响当前项。
            try:
                with session.begin_nested():
                    if lease is None:
                        lease = Lease(id=next_id(session, Lease, "LEASE"), workspace_id=WORKSPACE_ID,
                                      kind="manual", holder_subject=actor.id, purpose=purpose,
                                      status="active", start_at=started,
                                      expires_at=started + timedelta(hours=hours), hours=hours,
                                      request_key=request_key, request_hash=request_hash)
                        session.add(lease)
                        session.flush()
                    env_devices = [locked_devices[d] for d in env_device_ids(session, WORKSPACE_ID, env.id)]
                    session.add(LeaseEnvironment(
                        workspace_id=WORKSPACE_ID, lease_id=lease.id, environment_id=env.id,
                        name_snapshot=env.name, topology_version=env.topology_version,
                        device_ids_snapshot=[d.id for d in env_devices]))
                    for device in env_devices:
                        if device.id not in claimed:
                            session.add(LeaseResource(
                                workspace_id=WORKSPACE_ID, lease_id=lease.id, device_id=device.id,
                                active=True, device_snapshot={
                                    "name": device.name, "arch": device.architecture,
                                    "type": device.resource_type, "holder": actor.name})
                            )
                            claimed[device.id] = device
                    session.flush()
                selected_envs.append(env)
                outcomes.append({"envId": env_id, "name": env.name, "status": "borrowed",
                                 "code": "available", "reason": "空闲环境已直接借用，无需审批"})
                # 记入本批持有集合；不污染全局 occupancy（同批共享环境仍可成功）。
                for d in env_devices:
                    claimed[d.id] = d
            except IntegrityError:
                occupancy[env_id] = "leased"
                failure = {"code": "leased", "reason": REASONS["leased"]}
        if failure is not None:
            outcomes.append({"envId": env_id, "name": env.name if env else env_id,
                             "status": "skipped", **failure})

    lease_dict = None
    if lease is not None and selected_envs:
        lease_dict = lease_dict_of(session, lease)
        activity = f"{actor.name} 借用了 {'、'.join(e.name for e in selected_envs)}（{len(selected_envs)} 个环境）"
        record_event(session, actor_id=actor.id, action="lease_activity", entity_type="lease",
                     entity_id=lease.id, tone="green", payload={"text": activity})
    result = {"lease": lease_dict, "requestedEnvIds": requested, "outcomes": outcomes}
    record_event(session, actor_id=actor.id, action="batch_borrow_result", entity_type="lease",
                 entity_id=lease.id if lease else "none", tone="green" if lease_dict else "amber",
                 payload={"requestedEnvIds": requested}, result_snapshot=result,
                 request_key=request_key, request_hash=request_hash)
    session.flush()
    return result


def lease_dict_of(session: Session, lease: Lease) -> dict:
    env_rows = session.execute(
        select(LeaseEnvironment).where(LeaseEnvironment.lease_id == lease.id)
    ).scalars().all()
    device_rows = session.execute(
        select(LeaseResource.device_id).where(LeaseResource.lease_id == lease.id,
                                              LeaseResource.active.is_(True))
    ).scalars().all()
    holder = get_user(session, lease.holder_subject)
    return {
        "id": lease.id, "envIds": [r.environment_id for r in env_rows],
        "envNames": [r.name_snapshot for r in env_rows],
        "deviceIds": list(device_rows), "user": holder.name, "purpose": lease.purpose,
        "hours": lease.hours, "status": lease.status,
        "start": lease.start_at.isoformat(), "end": lease.expires_at.isoformat(),
        "kind": lease.kind,
    }


def _own_lease(session: Session, actor_id: str, lease_id: str) -> Lease:
    lease = session.get(Lease, lease_id)
    if not lease:
        raise BusinessError("借用记录不存在。")
    if lease.holder_subject != actor_id:
        raise BusinessError("只能操作自己的借用记录。")
    return lease


def renew(session: Session, actor_id: str, lease_id: str, hours: int = 4) -> dict:
    actor = get_user(session, actor_id)
    lease = _own_lease(session, actor.id, lease_id)
    if lease.status != "active":
        raise BusinessError("当前借用状态不可续期。")
    if hours not in VALID_HOURS:
        raise BusinessError("续期时长无效。")
    base = max(lease.expires_at, now())
    lease.expires_at = base + timedelta(hours=hours)
    record_event(session, actor_id=actor.id, action="lease_activity", entity_type="lease",
                 entity_id=lease.id, payload={"text": f"{lease.id} 已续期 {hours} 小时"})
    session.flush()
    return lease_dict_of(session, lease)


def _release_devices(session: Session, lease_id: str) -> list[str]:
    rows = session.execute(
        select(LeaseResource).where(LeaseResource.lease_id == lease_id, LeaseResource.active.is_(True))
    ).scalars().all()
    released = []
    for row in rows:
        row.active = False
        row.released_at = now()
        released.append(row.device_id)
    return released


def request_return(session: Session, actor_id: str, lease_id: str) -> dict:
    actor = get_user(session, actor_id)
    lease = _own_lease(session, actor.id, lease_id)
    if lease.status != "active":
        raise BusinessError("此借用已进入归还流程。")
    lease.status = "checking"
    record_event(session, actor_id=actor.id, action="lease_activity", entity_type="lease",
                 entity_id=lease.id, tone="amber",
                 payload={"text": f"{lease.id} 已提交归还，等待健康复核"})
    session.flush()
    return lease_dict_of(session, lease)


def verify_return(session: Session, actor_id: str, lease_id: str, healthy: bool) -> dict:
    """健康复核：通过整单释放；异常整单隔离并建维护记录。设备锁定保持到复核完成。"""
    actor = get_user(session, actor_id)
    lease = _own_lease(session, actor.id, lease_id)
    if lease.status != "checking":
        raise BusinessError("请先提交归还，完成后再进行健康复核。")
    env_rows = session.execute(
        select(LeaseEnvironment).where(LeaseEnvironment.lease_id == lease.id)
    ).scalars().all()
    device_ids = _release_devices(session, lease.id)
    from .maintenance import create_isolation_record
    if healthy:
        lease.status = "released"
        for device_id in device_ids:
            device = session.get(Device, device_id)
            if device:
                device.health_state = "healthy"
                device.health_checked_at = now()
                if device.operational_state == "isolated":
                    device.operational_state = "idle"
    else:
        lease.status = "isolated"
        for device_id in device_ids:
            device = session.get(Device, device_id)
            if device:
                device.operational_state = "isolated"
                device.health_state = "unhealthy"
        create_isolation_record(
            session, actor_id=actor.id,
            environment_ids=[r.environment_id for r in env_rows],
            device_ids=device_ids,
            reason=f"{lease.id} 归还健康检查异常，整单设备已隔离",
            source_lease_id=lease.id)
    record_event(session, actor_id=actor.id, action="lease_activity", entity_type="lease",
                 entity_id=lease.id, tone="green" if healthy else "red",
                 payload={"text": f"{'、'.join(r.name_snapshot for r in env_rows)} 健康复核"
                                  f"{'通过，已释放' if healthy else '异常，已隔离'}"})
    session.flush()
    return lease_dict_of(session, lease)
