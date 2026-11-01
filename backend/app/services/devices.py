"""设备领域服务：自助新增/编辑/移出（docs/02 自助资源池规则）。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import BusinessError
from ..models import Device, Environment, EnvironmentDevice
from ..state import device_occupancy, device_status
from .resource_locks import lock_resources
from . import WORKSPACE_ID, check_platform, get_user, next_id, record_event, require_admin, text_field


def _get_active(session: Session, device_id: str) -> Device:
    device = session.get(Device, device_id)
    if not device or device.lifecycle != "active":
        raise BusinessError(f"设备 {device_id} 不存在或已移出资源池。")
    return device


def referencing_environments(session: Session, device_id: str) -> list[Environment]:
    return list(session.execute(
        select(Environment)
        .join(EnvironmentDevice, EnvironmentDevice.environment_id == Environment.id)
        .where(EnvironmentDevice.device_id == device_id, Environment.lifecycle == "active")
    ).scalars().all())


def save_device(session: Session, actor_id: str, payload: dict) -> dict:
    """新增设备需管理员，编辑自助；忽略输入中的状态覆写；占用期间禁止编辑；隔离设备仅可改元数据。"""
    actor = get_user(session, actor_id)
    if payload.get("id"):
        lock_resources(session, device_ids=[payload["id"]])
    previous = _get_active(session, payload["id"]) if payload.get("id") else None
    isolated_metadata_only = False
    if previous and previous.lifecycle == "active":
        occupancy = device_occupancy(session, previous.workspace_id)
        if previous.id in occupancy or previous.operational_state == "maintenance":
            raise BusinessError("设备正在使用、维护或等待复核，释放后才能编辑。")
        # docs/02：隔离设备可改元数据，但修改不会解除隔离。
        isolated_metadata_only = previous.operational_state == "isolated" and previous.id not in occupancy
    if previous is None:
        actor = require_admin(session, actor_id, "新增设备")
    name = text_field(payload.get("name"), "设备名称", 60, required=True)
    cpu = text_field(payload.get("cpu"), "CPU 规格", 100)
    memory = text_field(payload.get("memory"), "内存规格", 60)
    location = text_field(payload.get("location"), "设备位置", 100)
    address = text_field(payload.get("ip") or payload.get("address"), "设备地址", 100)
    arch = payload.get("arch")
    type_ = payload.get("type")
    check_platform(arch, type_)
    host = text_field(payload.get("host"), "宿主机", 100) if type_ == "virtual" else ""
    if previous:
        if isolated_metadata_only and (previous.architecture != arch or previous.resource_type != type_):
            raise BusinessError("隔离设备只能修改元数据，不能改变架构或类型。")
        refs = referencing_environments(session, previous.id)
        if any(env.architecture != arch or env.resource_type != type_ for env in refs):
            raise BusinessError("设备仍被环境引用，请先调整关联后再修改架构或类型。")
    if previous is None:
        device = Device(id=next_id(session, Device, "DEV"), workspace_id=WORKSPACE_ID,
                        name=name, architecture=arch, resource_type=type_, cpu=cpu, memory=memory,
                        address=address, host=host, location=location, lifecycle="active",
                        operational_state="idle", health_state="unknown", created_by=actor.id,
                        row_version=1)
        session.add(device)
        action = "device_create"
    else:
        device = previous
        device.name, device.cpu, device.memory, device.address, device.location = name, cpu, memory, address, location
        device.host = host
        device.architecture, device.resource_type = arch, type_
        device.row_version += 1
        action = "device_update"
    record_event(session, actor_id=actor.id, action=action, entity_type="device", entity_id=device.id,
                 tone="green", payload={"name": name})
    session.flush()
    return {"id": device.id, "name": device.name}


def retire_device(session: Session, actor_id: str, device_id: str) -> dict:
    """移出设备：删除类危险操作，仅管理员；占用或仍被引用时拒绝。"""
    actor = require_admin(session, actor_id, "移出设备")
    lock_resources(session, device_ids=[device_id])
    device = _get_active(session, device_id)
    occupancy = device_occupancy(session, device.workspace_id)
    if device.id in occupancy or device.operational_state != "idle":
        raise BusinessError("设备仍有占用或维护，释放后才能移出资源池。")
    if referencing_environments(session, device_id):
        raise BusinessError("设备仍被环境引用，请先编辑关联环境或将环境移出资源池。")
    device.lifecycle = "retired"
    device.row_version += 1
    record_event(session, actor_id=actor.id, action="device_retire", entity_type="device",
                 entity_id=device.id, tone="amber", payload={"name": device.name})
    session.flush()
    return {"id": device.id, "name": device.name}


def device_dict(session: Session, device: Device, occupancy: dict[str, str]) -> dict:
    """mock 同构设备字典。"""
    return {
        "id": device.id, "name": device.name, "arch": device.architecture, "type": device.resource_type,
        "status": device_status(device, occupancy), "ip": device.address, "cpu": device.cpu,
        "memory": device.memory, "host": device.host, "location": device.location, "lifecycle": device.lifecycle,
        "health": device.health_state, "createdBy": device.created_by,
        "createdAt": device.created_at.isoformat(), "updatedAt": device.updated_at.isoformat(),
    }
