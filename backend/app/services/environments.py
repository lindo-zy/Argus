"""环境领域服务：建档、编辑、移出、恢复、关联调整、手动健康检查。

规则来源 docs/02 自助资源池 + 本轮 P0/P1 需求：
- 至少关联一台有效设备，架构/类型必须一致；设备可被多环境共享。
- 占用（借用/执行/维护/对账/清理/复核）期间禁止编辑配置或移出。
- 移出为 retired 生命周期变更，保留历史与稳定 ID；恢复入池重新校验。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import OP_IDLE, BusinessError
from ..models import Device, Environment, EnvironmentDevice, LeaseResource, Lease
from ..state import device_occupancy, device_status, env_device_ids, fmt_health
from . import WORKSPACE_ID, check_platform, get_user, next_id, now, record_event, require_admin, text_field
from .devices import _get_active as get_device
from .resource_locks import lock_resources


def _get_env(session: Session, env_id: str, include_retired: bool = False) -> Environment:
    env = session.get(Environment, env_id)
    if not env or (env.lifecycle == "retired" and not include_retired):
        raise BusinessError("环境不存在或已移出资源池。")
    return env


def _validate_devices(session: Session, env: Environment | None, device_ids: list[str],
                      arch: str, type_: str) -> list[Device]:
    if not device_ids or any(not isinstance(i, str) or not i.strip() for i in device_ids):
        raise BusinessError("请至少关联一台有效设备。")
    unique_ids = list(dict.fromkeys(device_ids))
    devices = []
    for device_id in unique_ids:
        device = get_device(session, device_id)
        if device.architecture != arch or device.resource_type != type_:
            raise BusinessError("关联设备的架构、类型必须与环境要求一致。")
        devices.append(device)
    workspace = env.workspace_id if env is not None else WORKSPACE_ID
    occupancy = device_occupancy(session, workspace)
    busy = [d.id for d in devices if d.id in occupancy or d.operational_state != OP_IDLE]
    if busy:
        raise BusinessError("关联设备正在使用或维护，请选择其他设备。")
    return devices


def _topology_set(session: Session, env: Environment, device_ids: list[str], actor_id: str) -> None:
    """整体替换当前拓扑并递增拓扑版本。"""
    session.execute(
        EnvironmentDevice.__table__.delete().where(
            EnvironmentDevice.workspace_id == WORKSPACE_ID,
            EnvironmentDevice.environment_id == env.id,
        )
    )
    for device_id in dict.fromkeys(device_ids):
        session.add(EnvironmentDevice(workspace_id=WORKSPACE_ID, environment_id=env.id,
                                      device_id=device_id, created_by=actor_id))
    env.topology_version += 1


def save_environment(session: Session, actor_id: str, payload: dict) -> dict:
    """新增环境需管理员，编辑自助；保存即生效（含复制场景：copy_of 携带来源名）。"""
    actor = get_user(session, actor_id)
    requested_ids = payload.get("deviceIds")
    lock_resources(session, [payload["id"]] if payload.get("id") else [],
                   requested_ids if isinstance(requested_ids, list) and all(isinstance(i, str) for i in requested_ids) else [])
    previous = _get_env(session, payload["id"]) if payload.get("id") else None
    if previous:
        _assert_editable(session, previous)
    else:
        actor = require_admin(session, actor_id, "新增环境")
    name = text_field(payload.get("name"), "环境名称", 60, required=True)
    description = text_field(payload.get("description"), "环境用途", 200)
    version = text_field(payload.get("version"), "系统版本", 80)
    location = text_field(payload.get("location"), "部署位置", 100)
    arch = payload.get("arch")
    type_ = payload.get("type")
    check_platform(arch, type_)
    devices = _validate_devices(session, previous, payload.get("deviceIds") or [], arch, type_)
    device_ids = [d.id for d in devices]
    tags = _normalize_tags(payload.get("tags"), arch, type_)
    owner = text_field(payload.get("owner"), "负责人", 60) or (previous.owner_subject if previous else actor.name)
    if previous is None:
        env = Environment(id=next_id(session, Environment, "ENV"), workspace_id=WORKSPACE_ID,
                          name=name, architecture=arch, resource_type=type_, description=description,
                          os_version_text=version, location=location, tags=tags,
                          owner_subject=owner, lifecycle="active", created_by=actor.id)
        session.add(env)
        action = "environment_create"
    else:
        env = previous
        env.name, env.description, env.os_version_text, env.location = name, description, version, location
        env.architecture, env.resource_type, env.tags, env.owner_subject = arch, type_, tags, owner
        same_topology = env_device_ids(session, WORKSPACE_ID, env.id) == device_ids
        action = "environment_update"
        if not same_topology:
            action = "environment_topology_update"
    _topology_set(session, env, device_ids, actor.id)
    env.row_version += 1
    record_event(session, actor_id=actor.id, action=action, entity_type="environment", entity_id=env.id,
                 tone="green", payload={"name": name, "deviceIds": device_ids})
    session.flush()
    return {"id": env.id, "name": env.name}


def _normalize_tags(raw: object, arch: str, type_: str) -> list[str]:
    """用户自定义标签 + 固定架构/类型标签（与 seed 语义一致）。"""
    base = [arch, "物理机" if type_ == "physical" else "虚拟机"]
    if raw is None:
        return base
    if not isinstance(raw, list) or any(not isinstance(t, str) for t in raw):
        raise BusinessError("标签格式无效。")
    custom = [t.strip() for t in raw if t.strip() and t not in base]
    return base + custom[:8]


def _occupying_leases(session: Session, env: Environment) -> list[str]:
    """占用该环境任一设备的 active 租约（含 checking）。"""
    device_ids = env_device_ids(session, WORKSPACE_ID, env.id)
    if not device_ids:
        return []
    rows = session.execute(
        select(LeaseResource.device_id).join(Lease, Lease.id == LeaseResource.lease_id).where(
            LeaseResource.workspace_id == WORKSPACE_ID,
            LeaseResource.active.is_(True),
            Lease.status.in_(["active", "checking"]),
            LeaseResource.device_id.in_(device_ids),
        )
    ).scalars().all()
    return list(rows)


def _assert_editable(session: Session, env: Environment) -> None:
    if _occupying_leases(session, env):
        raise BusinessError("环境设备正在使用、维护或等待复核，释放后才能调整资源配置。")
    devices = session.execute(
        select(Device).join(EnvironmentDevice, EnvironmentDevice.device_id == Device.id).where(
            EnvironmentDevice.environment_id == env.id, Device.lifecycle == "active")
    ).scalars().all()
    if any(d.operational_state != OP_IDLE for d in devices):
        raise BusinessError("环境设备正在使用、维护或等待复核，释放后才能调整资源配置。")


def adjust_devices(session: Session, actor_id: str, env_id: str, add: list[str], remove: list[str]) -> dict:
    """详情内快速调整关联（P0）：逐项校验，整批一个校验上下文。"""
    actor = get_user(session, actor_id)
    lock_resources(session, [env_id], add if isinstance(add, list) and all(isinstance(i, str) for i in add) else [])
    env = _get_env(session, env_id)
    _assert_editable(session, env)
    current = env_device_ids(session, WORKSPACE_ID, env.id)
    remove = list(dict.fromkeys(remove or []))
    add = list(dict.fromkeys(add or []))
    unknown = [i for i in remove if i not in current]
    if unknown:
        raise BusinessError(f"设备 {unknown[0]} 不在该环境的关联列表中。")
    target = [i for i in current if i not in remove] + add
    if not target:
        raise BusinessError("环境至少需要关联一台设备，不能全部摘除。")
    devices = _validate_devices(session, env, target, env.architecture, env.resource_type)
    _topology_set(session, env, [d.id for d in devices], actor.id)
    env.row_version += 1
    record_event(session, actor_id=actor.id, action="environment_devices_adjust",
                 entity_type="environment", entity_id=env.id, tone="blue",
                 payload={"added": add, "removed": remove})
    session.flush()
    return {"id": env.id, "deviceIds": env_device_ids(session, WORKSPACE_ID, env.id)}


def archive_environment(session: Session, actor_id: str, env_id: str) -> dict:
    """移出资源池：删除类危险操作，仅管理员；保留历史与稳定 ID。"""
    actor = require_admin(session, actor_id, "移出环境")
    lock_resources(session, [env_id])
    env = _get_env(session, env_id)
    _assert_editable(session, env)
    env.lifecycle = "retired"
    env.row_version += 1
    record_event(session, actor_id=actor.id, action="environment_retire",
                 entity_type="environment", entity_id=env.id, tone="amber",
                 payload={"name": env.name})
    session.flush()
    return {"id": env.id, "name": env.name}


def restore_environment(session: Session, actor_id: str, env_id: str) -> dict:
    """恢复入池（P0）：重新校验设备引用有效性、一致性与占用，全部满足才恢复。"""
    actor = require_admin(session, actor_id, "恢复入池")
    lock_resources(session, [env_id])
    env = session.get(Environment, env_id)
    if not env:
        raise BusinessError("环境不存在。")
    if env.lifecycle != "retired":
        raise BusinessError("该环境不在已移出状态，无需恢复。")
    device_ids = env_device_ids(session, WORKSPACE_ID, env.id)
    occupancy = device_occupancy(session, WORKSPACE_ID)
    problems: list[str] = []
    for device_id in device_ids:
        device = session.get(Device, device_id)
        if not device or device.lifecycle != "active":
            problems.append(f"关联设备 {device_id} 已移出或不存在，请先调整关联")
            continue
        if device.architecture != env.architecture or device.resource_type != env.resource_type:
            problems.append(f"设备 {device.name} 架构/类型与环境不一致")
            continue
        if device_id in occupancy or device.operational_state != OP_IDLE:
            problems.append(f"设备 {device.name} 正被占用或维护")
    if problems:
        raise BusinessError("无法恢复入池：" + "；".join(problems[:3]) + ("等" if len(problems) > 3 else "") + "。")
    if not device_ids:
        raise BusinessError("环境没有关联设备，请先编辑关联后再恢复。")
    env.lifecycle = "active"
    env.topology_version += 1
    env.row_version += 1
    record_event(session, actor_id=actor.id, action="environment_restore",
                 entity_type="environment", entity_id=env.id, tone="green",
                 payload={"name": env.name})
    session.flush()
    return {"id": env.id, "name": env.name}


def copy_environment(session: Session, actor_id: str, env_id: str, device_ids: list[str] | None = None) -> dict:
    """以现有环境为模板复制建档（P2）：默认不带设备，需显式选择。"""
    source = _get_env(session, env_id, include_retired=True)
    payload = {
        "name": f"{source.name} 副本", "arch": source.architecture, "type": source.resource_type,
        "description": source.description, "version": source.os_version_text,
        "location": source.location, "tags": [t for t in (source.tags or [])],
        "owner": source.owner_subject, "deviceIds": device_ids or [],
    }
    if not device_ids:
        payload["deviceIds"] = []
        # 复制允许先建空关联？规则要求至少一台设备——复制场景改为调用方传设备，否则沿用校验报错。
    return save_environment(session, actor_id, payload)


def health_check(session: Session, actor_id: str, env_id: str, healthy: bool, note: str = "") -> dict:
    """手动健康检查（P1）：仅空闲环境；异常转隔离并建立维护记录。"""
    actor = get_user(session, actor_id)
    lock_resources(session, [env_id])
    env = _get_env(session, env_id)
    occupancy = device_occupancy(session, WORKSPACE_ID)
    device_ids = env_device_ids(session, WORKSPACE_ID, env.id)
    devices = [session.get(Device, i) for i in device_ids]
    busy = [d.id for d in devices if d and (d.id in occupancy or d.operational_state == "maintenance")]
    if busy:
        raise BusinessError("环境正在占用或维护中，不能发起健康检查。")
    checked_at = now()
    evidence = {"note": text_field(note, "检查说明", 200), "checkedBy": actor.name,
                "checkedAt": checked_at.isoformat(), "deviceIds": device_ids}
    from .maintenance import create_isolation_record
    for device in devices:
        if not device:
            continue
        device.health_state = "healthy" if healthy else "unhealthy"
        device.health_checked_at = checked_at
        if not healthy:
            device.operational_state = "isolated"
    record_event(session, actor_id=actor.id, action="environment_health_check",
                 entity_type="environment", entity_id=env.id,
                 tone="green" if healthy else "red", payload={**evidence, "healthy": healthy})
    if not healthy:
        create_isolation_record(session, actor_id=actor.id, environment_ids=[env.id],
                                device_ids=device_ids,
                                reason=f"{env.name} 手动健康检查异常，设备已隔离", evidence=evidence)
    session.flush()
    return {"id": env.id, "healthy": healthy, "checkedAt": checked_at.isoformat()}


def environment_dict(session: Session, env: Environment, occupancy: dict[str, str]) -> dict:
    """mock 同构环境字典（保留 suite 兼容字段）。"""
    device_ids = env_device_ids(session, WORKSPACE_ID, env.id)
    devices = [session.get(Device, i) for i in device_ids]
    states = [device_status(d, occupancy) for d in devices if d]
    status = "available"
    from ..errors import STATUS_PRIORITY
    for candidate in STATUS_PRIORITY:
        if candidate in states:
            status = candidate
            break
    checked = [d.health_checked_at for d in devices if d and d.health_checked_at]
    health = fmt_health(max(checked)) if checked and all(d and d.health_state == "healthy" and d.health_checked_at for d in devices) else "待检查"
    if any(d and d.health_state == "unhealthy" for d in devices):
        health = "待复核"
    return {
        "id": env.id, "name": env.name, "arch": env.architecture, "type": env.resource_type,
        "deviceIds": device_ids, "ip": ", ".join(dict.fromkeys(d.address for d in devices if d and d.address)),
        "host": ", ".join(dict.fromkeys(d.host for d in devices if d and d.host)),
        "description": env.description, "suite": "",
        "version": env.os_version_text or "", "owner": env.owner_subject, "tags": env.tags or [],
        "health": health, "location": env.location, "lifecycle": env.lifecycle,
        "status": status, "topologyVersion": env.topology_version,
        "createdAt": env.created_at.isoformat(), "updatedAt": env.updated_at.isoformat(),
    }
