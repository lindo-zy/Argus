"""维护领域服务：自助维护、复核恢复/隔离、隔离记录（docs/02 归还、维护与执行）。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import BusinessError
from ..models import Device, EnvironmentDevice, LeaseResource, Lease, MaintenanceRecord
from ..state import device_occupancy, env_device_ids
from . import WORKSPACE_ID, get_user, next_id, now, record_event, text_field
from .environments import _get_env
from .resource_locks import lock_resources


def create_isolation_record(session: Session, *, actor_id: str, environment_ids: list[str],
                            device_ids: list[str], reason: str,
                            source_lease_id: str | None = None,
                            evidence: dict | None = None) -> MaintenanceRecord:
    """异常隔离事实登记：归还异常、清理异常、手动健康检查异常共用。"""
    record = MaintenanceRecord(
        id=next_id(session, MaintenanceRecord, "MT"), workspace_id=WORKSPACE_ID,
        lease_id=source_lease_id, source_lease_id=source_lease_id,
        environment_ids=environment_ids, device_ids=device_ids, reason=reason,
        status="isolated", result="unhealthy", verified_by=actor_id,
        verified_at=now(), created_by=actor_id, verification_evidence=evidence or {})
    session.add(record)
    return record


def start(session: Session, actor_id: str, payload: dict) -> dict:
    """空闲环境进入维护；共享设备关联环境同步不可借（状态派生自动生效）。"""
    actor = get_user(session, actor_id)
    reason = text_field(payload.get("reason"), "维护原因", 200, required=True)
    lock_resources(session, [payload.get("envId") or ""])
    env = _get_env(session, payload.get("envId") or "")
    occupancy = device_occupancy(session, WORKSPACE_ID)
    device_ids = env_device_ids(session, WORKSPACE_ID, env.id)
    devices = [session.get(Device, i) for i in device_ids]
    for device in devices:
        if device is None:
            raise BusinessError("环境存在无效设备关联，请先调整。")
        if device.id in occupancy or device.operational_state != "idle":
            raise BusinessError("只能对空闲环境开始维护。")
    record = MaintenanceRecord(id=next_id(session, MaintenanceRecord, "MT"),
                               workspace_id=WORKSPACE_ID, environment_ids=[env.id],
                               device_ids=device_ids, reason=reason, status="maintenance",
                               created_by=actor.id)
    session.add(record)
    for device in devices:
        device.operational_state = "maintenance"
    record_event(session, actor_id=actor.id, action="maintenance_start", entity_type="maintenance",
                 entity_id=record.id, tone="amber", payload={"envId": env.id, "name": env.name})
    session.flush()
    return {"id": record.id, "envId": env.id}


def _scope_devices(session: Session, record: MaintenanceRecord) -> list[Device]:
    device_ids = record.device_ids or []
    return [d for d in (session.get(Device, i) for i in device_ids) if d]


def verify(session: Session, actor_id: str, record_id: str, healthy: bool) -> dict:
    """健康复核（资源状态检查，不是审批）：通过恢复可用；异常保持隔离。"""
    actor = get_user(session, actor_id)
    record = session.get(MaintenanceRecord, record_id)
    if not record or record.status not in ("maintenance", "isolated"):
        raise BusinessError("维护记录已结束。")
    # 有共享设备仍被其他租约占用时不能复核释放（docs/02）。
    lock_resources(session, device_ids=record.device_ids or [])
    occupancy = device_occupancy(session, WORKSPACE_ID)
    scope = _scope_devices(session, record)
    external = [d.id for d in scope if d.id in occupancy]
    if external:
        raise BusinessError("仍有共享设备占用，无法完成健康复核。")
    for device in scope:
        if healthy:
            device.operational_state = "idle"
            device.health_state = "healthy"
            device.health_checked_at = now()
        else:
            device.operational_state = "isolated"
    record.status = "success" if healthy else "isolated"
    record.result = "healthy" if healthy else "unhealthy"
    record.verified_by = actor.id
    record.verified_at = now()
    record.verification_evidence = {"verifiedBy": actor.name, "mode": "manual-demo"}
    names = "、".join(record.environment_ids or [])
    record_event(session, actor_id=actor.id, action="maintenance_verify", entity_type="maintenance",
                 entity_id=record.id, tone="green" if healthy else "red",
                 payload={"text": f"{names} 维护复核{'通过，恢复可用' if healthy else '异常，保持隔离'}"})
    session.flush()
    return {"id": record.id, "status": record.status}


def maintenance_dict(session: Session, record: MaintenanceRecord) -> dict:
    from ..models import User
    creator = session.get(User, record.created_by)
    return {
        "id": record.id, "envIds": record.environment_ids or [], "deviceIds": record.device_ids or [],
        "reason": record.reason, "status": record.status, "issue": record.issue_ref,
        "user": creator.name if creator else record.created_by,
        "date": record.created_at.isoformat(),
    }
