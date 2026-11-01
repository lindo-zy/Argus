"""跨环境拓扑调整：预期关联校验、状态保护、整批原子提交与审计。"""
from sqlalchemy.orm import Session

from ..errors import BusinessError
from ..models import Device
from ..state import env_device_ids
from . import WORKSPACE_ID, get_user, record_event
from .environments import _get_env, _assert_editable, _validate_devices, _topology_set
from .resource_locks import lock_resources


def save_topology(session: Session, actor_id: str, payload: dict) -> dict:
    actor = get_user(session, actor_id)
    changes = payload.get("changes")
    if not isinstance(changes, list) or not 1 <= len(changes) <= 100:
        raise BusinessError("请提交 1–100 个环境的关联变化。")
    env_ids, requested_devices = [], set()
    for change in changes:
        if not isinstance(change, dict) or not isinstance(change.get("envId"), str) or not change["envId"]:
            raise BusinessError("环境关联参数无效。")
        if change["envId"] in env_ids:
            raise BusinessError("同一环境只能提交一次关联变化。")
        env_ids.append(change["envId"])
        for key in ("expectedDeviceIds", "deviceIds"):
            ids = change.get(key)
            if not isinstance(ids, list) or not ids or any(not isinstance(i, str) or not i.strip() for i in ids):
                raise BusinessError("环境至少需要关联一台有效设备，并提供原关联快照。")
            if len(ids) != len(set(ids)):
                raise BusinessError("设备关联不能重复。")
            requested_devices.update(ids)
    lock_resources(session, env_ids, requested_devices)
    prepared = []
    for change in changes:
        env = _get_env(session, change["envId"])
        current = env_device_ids(session, WORKSPACE_ID, env.id)
        if set(current) != set(change["expectedDeviceIds"]):
            raise BusinessError("环境拓扑已变化，请刷新数据后重新调整。", code="topology_conflict")
        target = change["deviceIds"]
        if set(current) == set(target):
            continue
        _assert_editable(session, env)
        _validate_devices(session, env, target, env.architecture, env.resource_type)
        prepared.append((env, current, target))
    # 所有环境先通过校验才开始写入，任一失败均不改变关联。
    for env, current, target in prepared:
        _topology_set(session, env, target, actor.id)
        env.row_version += 1
        for device_id in set(current) | set(target):
            device = session.get(Device, device_id)
            device.health_state, device.health_checked_at = "unknown", None
        record_event(session, actor_id=actor.id, action="environment_topology_update",
                     entity_type="environment", entity_id=env.id,
                     payload={"name": env.name, "before": current, "deviceIds": target,
                              "added": sorted(set(target) - set(current)),
                              "removed": sorted(set(current) - set(target))})
    session.flush()
    return {"environmentIds": [env.id for env, _, _ in prepared], "changed": len(prepared)}
