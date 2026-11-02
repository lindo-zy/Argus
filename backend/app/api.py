"""REST API：薄路由层，全部委托领域服务；变更统一返回 { result, snapshot }。

身份：X-Argus-User 请求头（演示级）。MCP 入口走同一服务层，见 mcp_server.py。
"""
from __future__ import annotations

import io
import csv
from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_session
from .errors import BusinessError
from .models import (
    Device, Environment, EnvironmentDevice, PipelineDefinition, PipelineRun,
    PipelineVersion, Lease, LeaseEnvironment, LeaseResource, MaintenanceRecord,
    ResourceEvent, User,
)
from .services import WORKSPACE_ID, require_admin, revision_of
from .services import devices as device_service
from .services import environments as env_service
from .services import insights, leases as lease_service
from .services import maintenance as maintenance_service
from .services import runs as run_service
from .services import pipelines as pipeline_service
from .services import snapshot as snapshot_service
from .services import users as user_service
from .services.topology import save_topology as save_topology_service
from .seed import is_empty, seed

router = APIRouter(prefix="/api")


def actor_id_header(x_argus_user: str = Header(default="")) -> str:
    return x_argus_user


def commit_and_respond(session: Session, result: Any) -> dict:
    session.commit()
    return {"result": result, "snapshot": snapshot_service.build_snapshot(session)}


@router.post('/pipelines/drafts')
def save_pipeline_draft(payload: dict, session: Session = Depends(get_session),
                        actor: str = Depends(actor_id_header)) -> dict:
    return commit_and_respond(session, pipeline_service.save_pipeline(session, actor, payload))


@router.post('/pipelines/validate')
def validate_pipeline_draft(payload: dict, session: Session = Depends(get_session),
                            actor: str = Depends(actor_id_header)) -> dict:
    return pipeline_service.validate_pipeline(session, actor, payload)


@router.post('/pipelines/{pipeline_id}/publish')
def publish_pipeline(pipeline_id: str, payload: dict, session: Session = Depends(get_session),
                     actor: str = Depends(actor_id_header)) -> dict:
    return commit_and_respond(session, pipeline_service.publish_pipeline(session, actor, pipeline_id, payload))


@router.post('/pipelines/{pipeline_id}/sync')
def sync_pipeline_job(pipeline_id: str, session: Session = Depends(get_session),
                      actor: str = Depends(actor_id_header)) -> dict:
    """把已发布编排重新同步为 Jenkins Job（创建/更新幂等）；Jenkins 不可达返回当前同步态。"""
    from .services.provisioning import sync_published_job
    return commit_and_respond(session, sync_published_job(session, actor, pipeline_id))


@router.get("/snapshot")
def get_snapshot(session: Session = Depends(get_session)) -> dict:
    snap = snapshot_service.build_snapshot(session)
    snap["revision"] = revision_of(session)
    return snap


@router.post("/reset")
def reset_demo(session: Session = Depends(get_session), actor: str = Depends(actor_id_header)) -> dict:
    """恢复初始演示样本（删除类，仅管理员）：清空业务表后重新 seed。"""
    require_admin(session, actor, "重置演示数据")
    for table in (ResourceEvent, LeaseResource, LeaseEnvironment, Lease, MaintenanceRecord,
                  PipelineRun, PipelineVersion, PipelineDefinition,
                  EnvironmentDevice, Environment, Device, User):
        session.query(table).delete()
    session.commit()
    seed(session)
    session.commit()
    return {"result": {"reset": True}, "snapshot": snapshot_service.build_snapshot(session)}


@router.post("/topology")
def save_topology(payload: dict, session: Session = Depends(get_session),
                  actor: str = Depends(actor_id_header)) -> dict:
    return commit_and_respond(session, save_topology_service(session, actor, payload))


# ---- 环境域 ----

@router.post("/environments")
def save_environment(payload: dict, session: Session = Depends(get_session),
                     actor: str = Depends(actor_id_header)) -> dict:
    result = env_service.save_environment(session, actor, payload)
    return commit_and_respond(session, result)


@router.post("/environments/{env_id}/retire")
def retire_environment(env_id: str, session: Session = Depends(get_session),
                       actor: str = Depends(actor_id_header)) -> dict:
    result = env_service.archive_environment(session, actor, env_id)
    return commit_and_respond(session, result)


@router.post("/environments/{env_id}/restore")
def restore_environment(env_id: str, session: Session = Depends(get_session),
                        actor: str = Depends(actor_id_header)) -> dict:
    result = env_service.restore_environment(session, actor, env_id)
    return commit_and_respond(session, result)


@router.post("/environments/{env_id}/devices")
def adjust_environment_devices(env_id: str, payload: dict,
                               session: Session = Depends(get_session),
                               actor: str = Depends(actor_id_header)) -> dict:
    result = env_service.adjust_devices(session, actor, env_id,
                                        payload.get("add") or [], payload.get("remove") or [])
    return commit_and_respond(session, result)


@router.post("/environments/{env_id}/health-check")
def health_check(env_id: str, payload: dict, session: Session = Depends(get_session),
                 actor: str = Depends(actor_id_header)) -> dict:
    result = env_service.health_check(session, actor, env_id,
                                      bool(payload.get("healthy", True)),
                                      payload.get("note") or "")
    return commit_and_respond(session, result)


@router.post("/environments/{env_id}/copy")
def copy_environment(env_id: str, payload: dict, session: Session = Depends(get_session),
                     actor: str = Depends(actor_id_header)) -> dict:
    result = env_service.copy_environment(session, actor, env_id, payload.get("deviceIds") or [])
    return commit_and_respond(session, result)


@router.get("/environments/{env_id}/timeline")
def environment_timeline(env_id: str, session: Session = Depends(get_session),
                         limit: int = Query(default=100, le=500)) -> dict:
    return {"envId": env_id, "items": insights.environment_timeline(session, env_id, limit)}


@router.get("/environments/{env_id}/utilization")
def environment_utilization(env_id: str, session: Session = Depends(get_session),
                            days: int = Query(default=7, le=90)) -> dict:
    return insights.environment_utilization(session, env_id, days)


@router.get("/environments")
def list_environments(session: Session = Depends(get_session),
                      include_retired: bool = False,
                      arch: str = "", type: str = "", tag: str = "",
                      owner: str = "", location: str = "", version: str = "",
                      query: str = "") -> dict:
    stmt = select(Environment).where(Environment.workspace_id == WORKSPACE_ID)
    if not include_retired:
        stmt = stmt.where(Environment.lifecycle == "active")
    if arch:
        stmt = stmt.where(Environment.architecture == arch)
    if type:
        stmt = stmt.where(Environment.resource_type == type)
    if owner:
        stmt = stmt.where(Environment.owner_subject == owner)
    if location:
        stmt = stmt.where(Environment.location == location)
    if version:
        stmt = stmt.where(Environment.os_version_text == version)
    if tag:
        stmt = stmt.where(Environment.tags.contains([tag]))
    envs = session.execute(stmt).scalars().all()
    if query:
        q = query.lower()
        envs = [e for e in envs if q in f"{e.id} {e.name} {e.description}".lower()]
    from .state import device_occupancy
    occupancy = device_occupancy(session, WORKSPACE_ID)
    return {"environments": [env_service.environment_dict(session, e, occupancy) for e in envs]}


# ---- 设备域 ----

@router.post("/devices")
def save_device(payload: dict, session: Session = Depends(get_session),
                actor: str = Depends(actor_id_header)) -> dict:
    result = device_service.save_device(session, actor, payload)
    return commit_and_respond(session, result)


@router.post("/devices/{device_id}/retire")
def retire_device(device_id: str, session: Session = Depends(get_session),
                  actor: str = Depends(actor_id_header)) -> dict:
    result = device_service.retire_device(session, actor, device_id)
    return commit_and_respond(session, result)


# ---- 借用域 ----

@router.post("/leases/batch")
def borrow(payload: dict, session: Session = Depends(get_session),
           actor: str = Depends(actor_id_header),
           x_argus_request_key: str = Header(default="")) -> dict:
    result = lease_service.borrow(session, actor, payload, request_key=x_argus_request_key)
    return commit_and_respond(session, result)


@router.post("/leases/{lease_id}/renew")
def renew_lease(lease_id: str, payload: dict, session: Session = Depends(get_session),
                actor: str = Depends(actor_id_header)) -> dict:
    result = lease_service.renew(session, actor, lease_id, int(payload.get("hours", 4)))
    return commit_and_respond(session, result)


@router.post("/leases/{lease_id}/return")
def request_return(lease_id: str, session: Session = Depends(get_session),
                   actor: str = Depends(actor_id_header)) -> dict:
    result = lease_service.request_return(session, actor, lease_id)
    return commit_and_respond(session, result)


@router.post("/leases/{lease_id}/verify-return")
def verify_return(lease_id: str, payload: dict, session: Session = Depends(get_session),
                  actor: str = Depends(actor_id_header)) -> dict:
    result = lease_service.verify_return(session, actor, lease_id, bool(payload.get("healthy", True)))
    return commit_and_respond(session, result)


# ---- 维护域 ----

@router.post("/maintenance")
def start_maintenance(payload: dict, session: Session = Depends(get_session),
                      actor: str = Depends(actor_id_header)) -> dict:
    result = maintenance_service.start(session, actor, payload)
    return commit_and_respond(session, result)


@router.post("/maintenance/{record_id}/verify")
def verify_maintenance(record_id: str, payload: dict, session: Session = Depends(get_session),
                       actor: str = Depends(actor_id_header)) -> dict:
    result = maintenance_service.verify(session, actor, record_id, bool(payload.get("healthy", True)))
    return commit_and_respond(session, result)


# ---- 执行域 ----

@router.post("/runs")
def start_run(payload: dict, session: Session = Depends(get_session),
              actor: str = Depends(actor_id_header)) -> dict:
    result = run_service.start_run(session, actor, payload)
    return commit_and_respond(session, result)


@router.post("/runs/reconcile")
def reconcile_runs(session: Session = Depends(get_session)) -> dict:
    """手动对账：queue/build 状态回同步与终态清理（后台调度器的同款入口）。"""
    return commit_and_respond(session, run_service.reconcile_active_runs(session))


@router.post("/runs/{run_id}/finish")
def finish_run(run_id: str, payload: dict, session: Session = Depends(get_session),
               actor: str = Depends(actor_id_header)) -> dict:
    result = run_service.finish_run(session, actor, run_id, bool(payload.get("success", True)))
    return commit_and_respond(session, result)


@router.post("/runs/{run_id}/cleanup")
def cleanup_run(run_id: str, payload: dict, session: Session = Depends(get_session),
                actor: str = Depends(actor_id_header)) -> dict:
    result = run_service.cleanup_run(session, actor, run_id, bool(payload.get("healthy", True)))
    return commit_and_respond(session, result)


# ---- 人员与权限 ----

@router.get("/users")
def list_users(session: Session = Depends(get_session)) -> dict:
    return {"users": [user_service.user_dict(u) for u in user_service.list_users(session)]}


@router.post("/users")
def add_user(payload: dict, session: Session = Depends(get_session),
             actor: str = Depends(actor_id_header)) -> dict:
    result = user_service.add_user(session, actor, payload)
    return commit_and_respond(session, result)


@router.post("/users/{user_id}/role")
def set_user_role(user_id: str, payload: dict, session: Session = Depends(get_session),
                  actor: str = Depends(actor_id_header)) -> dict:
    result = user_service.set_role(session, actor, user_id, payload.get("role", "member"))
    return commit_and_respond(session, result)


@router.post("/users/{user_id}/status")
def set_user_status(user_id: str, payload: dict, session: Session = Depends(get_session),
                    actor: str = Depends(actor_id_header)) -> dict:
    result = user_service.set_status(session, actor, user_id, payload.get("status", "active"))
    return commit_and_respond(session, result)


# ---- 查询与报表 ----

@router.get("/events")
def list_events(session: Session = Depends(get_session), entity_type: str = "",
                entity_id: str = "", limit: int = Query(default=50, le=500),
                before_id: int = 0) -> dict:
    stmt = select(ResourceEvent).where(ResourceEvent.workspace_id == WORKSPACE_ID)
    if entity_type:
        stmt = stmt.where(ResourceEvent.entity_type == entity_type)
    if entity_id:
        stmt = stmt.where(ResourceEvent.entity_id == entity_id)
    if before_id:
        stmt = stmt.where(ResourceEvent.id < before_id)
    rows = session.execute(stmt.order_by(ResourceEvent.id.desc()).limit(limit)).scalars().all()
    actor_names = {u.id: u.name for u in session.execute(select(User)).scalars().all()}
    return {"events": [{
        "id": e.id, "action": e.action, "entityType": e.entity_type, "entityId": e.entity_id,
        "actor": actor_names.get(e.actor_subject, e.actor_subject), "tone": e.tone,
        "text": (e.payload or {}).get("text", ""), "occurredAt": e.occurred_at.isoformat(),
    } for e in rows]}


@router.get("/reports/distribution")
def report_distribution(session: Session = Depends(get_session)) -> dict:
    return insights.distribution(session)


@router.get("/reports/status-flow")
def report_status_flow(session: Session = Depends(get_session), days: int = Query(default=7, le=90)) -> dict:
    return insights.status_flow(session, days)


@router.get("/export/environments.csv")
def export_environments(session: Session = Depends(get_session), include_retired: bool = False,
                        arch: str = "", type: str = "", tag: str = "", owner: str = "",
                        location: str = "", version: str = "", query: str = "") -> Response:
    data = list_environments(session, include_retired, arch, type, tag, owner, location, version, query)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["ID", "名称", "IP", "宿主机", "架构", "类型", "状态", "设备数", "系统版本", "负责人",
                     "标签", "位置", "生命周期"])
    for env in data["environments"]:
        writer.writerow([env["id"], env["name"], env["ip"], env["host"], env["arch"],
                         "物理机" if env["type"] == "physical" else "虚拟机", env["status"],
                         len(env["deviceIds"]), env["version"], env["owner"],
                         " ".join(env["tags"]), env["location"], env["lifecycle"]])
    return Response(content=buffer.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=environments.csv"})
