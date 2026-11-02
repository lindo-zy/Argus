"""MCP server：把平台业务能力以工具形式暴露给大模型（streamable HTTP，挂载于 /mcp）。

与 REST 共用领域服务层与权限模型；身份取 Bearer token（users.api_token），
默认回退到演示管理员 token，便于本地体验。
"""
from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.context import Context
from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy.orm import Session

from .config import settings
from .db import SessionLocal
from .errors import BusinessError
from .models import User
from .services import users as user_service
from .services import environments as env_service
from .services import devices as device_service
from .services import insights, leases as lease_service
from .services import maintenance as maintenance_service
from .services import runs as run_service
from .services import pipelines as pipeline_service
from .services import snapshot as snapshot_service
from .services.topology import save_topology as save_topology_service
from .state import device_occupancy

mcp: MCPServer = MCPServer("argus", instructions=(
    "Argus 操作系统测试平台。工具覆盖环境/设备/借用/维护/执行的查询与操作；"
    "所有变更即时生效并写入审计事件。删除类操作（移出/恢复/重置）与人员管理仅管理员 token 可执行。"
))


@mcp.tool(description='查询演示用例目录：稳定 ID、版本、分类与架构；尚未接入真实用例中心。')
def list_pipeline_cases(ctx: Context = None) -> list[dict]:
    def handler(session):
        _actor(session, ctx)
        return pipeline_service.CASE_CATALOG
    return _run(handler)


@mcp.tool(description='保存标准流水线 DAG 草稿；仅环境安装和用例执行节点。更新需 expectedRevision；已发布配置不可修改。不会占用设备或执行任务。')
def save_pipeline_draft(payload: dict, ctx: Context = None) -> dict:
    return _run(lambda session: pipeline_service.save_pipeline(session, _actor(session, ctx).id, payload))


@mcp.tool(description='校验 DAG 结构、环境继承与已选用例版本；不会执行任务或占用设备。')
def validate_pipeline_draft(payload: dict, ctx: Context = None) -> dict:
    return _run(lambda session: pipeline_service.validate_pipeline(session, _actor(session, ctx).id, payload))


def _session() -> Session:
    return SessionLocal()


def _actor(session: Session, ctx: Context | None) -> User:
    headers = (ctx.headers if ctx is not None else None) or {}
    token = (headers.get("authorization") or "").removeprefix("Bearer ").strip()
    user = user_service.resolve_by_token(session, token) if token else None
    if user is None:
        user = user_service.resolve_by_token(session, settings().bootstrap_admin_token)
    if user is None:
        raise BusinessError("未找到有效身份，请配置正确的 MCP Bearer token。", code="unknown_actor")
    return user


def _run(handler):
    """统一会话管理：成功提交；业务异常回滚并以 ToolError 透传中文消息（SDK 2.x 只透传 ToolError）。"""
    session = _session()
    try:
        result = handler(session)
        session.commit()
        return result
    except BusinessError as exc:
        session.rollback()
        raise ToolError(exc.message) from exc
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@mcp.tool(description="查询环境列表，可按架构、类型、标签、负责人、关键字筛选；include_retired 查看已移出档案。")
def list_environments(arch: str = "", type: str = "", tag: str = "", owner: str = "",
                      query: str = "", include_retired: bool = False,
                      ctx: Context = None) -> list[dict]:
    def handler(session: Session) -> list[dict]:
        _actor(session, ctx)
        from .api import list_environments as _impl
        return _impl(session=session, include_retired=include_retired, arch=arch,
                     type=type, tag=tag, owner=owner, query=query)["environments"]
    return _run(handler)


@mcp.tool(description="查询单个环境详情：基础信息、关联设备、派生状态。")
def get_environment(env_id: str, ctx: Context = None) -> dict:
    def handler(session: Session) -> dict:
        _actor(session, ctx)
        from .models import Environment
        env = session.get(Environment, env_id)
        if not env:
            raise BusinessError("环境不存在。")
        return env_service.environment_dict(session, env, device_occupancy(session, env.workspace_id))
    return _run(handler)


@mcp.tool(description="查询环境历史时间线：借用、执行、维护与关键事件聚合。")
def get_environment_timeline(env_id: str, limit: int = 50, ctx: Context = None) -> dict:
    def handler(session: Session) -> dict:
        _actor(session, ctx)
        return {"envId": env_id, "items": insights.environment_timeline(session, env_id, limit)}
    return _run(handler)


@mcp.tool(description="新增或更新环境（自助，无需审批）。需要 name、arch（ARM64/x86_64）、type（physical/virtual）、至少一台 deviceIds。")
def save_environment(name: str, arch: str, type: str, deviceIds: list[str],
                     env_id: str = "", description: str = "", version: str = "",
                     location: str = "", owner: str = "", tags: list[str] | None = None,
                     ctx: Context = None) -> dict:
    def handler(session: Session) -> dict:
        actor = _actor(session, ctx)
        payload: dict[str, Any] = {"id": env_id, "name": name, "arch": arch, "type": type,
                                   "deviceIds": deviceIds, "description": description,
                                   "version": version, "location": location, "owner": owner,
                                   "tags": tags or []}
        return env_service.save_environment(session, actor.id, payload)
    return _run(handler)


@mcp.tool(description="原子调整多个环境的设备拓扑；changes 含 envId、expectedDeviceIds 原关联和 deviceIds 目标关联。占用或快照冲突时整批拒绝。")
def save_environment_topology(changes: list[dict[str, Any]], ctx: Context = None) -> dict:
    def handler(session: Session) -> dict:
        actor = _actor(session, ctx)
        return save_topology_service(session, actor.id, {"changes": changes})
    return _run(handler)


@mcp.tool(description="调整环境设备关联：add/remove 各为设备 ID 数组；占用设备不可摘除。")
def adjust_environment_devices(env_id: str, add: list[str] | None = None,
                               remove: list[str] | None = None, ctx: Context = None) -> dict:
    def handler(session: Session) -> dict:
        actor = _actor(session, ctx)
        return env_service.adjust_devices(session, actor.id, env_id, add or [], remove or [])
    return _run(handler)


@mcp.tool(description="移出环境（删除类危险操作，仅管理员）：retired 保留历史，可用 restore 恢复。")
def retire_environment(env_id: str, ctx: Context = None) -> dict:
    def handler(session: Session) -> dict:
        actor = _actor(session, ctx)
        return env_service.archive_environment(session, actor.id, env_id)
    return _run(handler)


@mcp.tool(description="恢复已移出环境入池（仅管理员）：自动重验设备引用与占用。")
def restore_environment(env_id: str, ctx: Context = None) -> dict:
    def handler(session: Session) -> dict:
        actor = _actor(session, ctx)
        return env_service.restore_environment(session, actor.id, env_id)
    return _run(handler)


@mcp.tool(description="对空闲环境发起手动健康检查：healthy=false 将隔离设备并建立维护记录。")
def check_environment_health(env_id: str, healthy: bool = True, note: str = "",
                             ctx: Context = None) -> dict:
    def handler(session: Session) -> dict:
        actor = _actor(session, ctx)
        return env_service.health_check(session, actor.id, env_id, healthy, note)
    return _run(handler)


@mcp.tool(description="查询设备列表（含派生状态）。")
def list_devices(ctx: Context = None) -> list[dict]:
    def handler(session: Session) -> list[dict]:
        _actor(session, ctx)
        from .models import Device
        from . import WORKSPACE_ID
        devices = session.query(Device).filter(Device.workspace_id == WORKSPACE_ID,
                                               Device.lifecycle == "active").order_by(Device.id).all()
        occupancy = device_occupancy(session, WORKSPACE_ID)
        return [device_service.device_dict(session, d, occupancy) for d in devices]
    return _run(handler)


@mcp.tool(description="新增设备（自助）：name、arch、type 必填，规格可选。")
def create_device(name: str, arch: str, type: str, cpu: str = "", memory: str = "",
                  ip: str = "", location: str = "", ctx: Context = None) -> dict:
    def handler(session: Session) -> dict:
        actor = _actor(session, ctx)
        return device_service.save_device(session, actor.id, {
            "name": name, "arch": arch, "type": type, "cpu": cpu, "memory": memory,
            "ip": ip, "location": location})
    return _run(handler)


@mcp.tool(description="批量借用环境（空闲直借、逐项部分成功）：hours ∈ 2/4/8/24/48，purpose 必填。")
def borrow_environments(envIds: list[str], hours: int = 4, purpose: str = "",
                        ctx: Context = None) -> dict:
    def handler(session: Session) -> dict:
        actor = _actor(session, ctx)
        return lease_service.borrow(session, actor.id, {"envIds": envIds, "hours": hours,
                                                        "purpose": purpose})
    return _run(handler)


@mcp.tool(description="归还自己的借用单：整单进入健康复核，不立即释放。")
def return_lease(lease_id: str, ctx: Context = None) -> dict:
    def handler(session: Session) -> dict:
        actor = _actor(session, ctx)
        return lease_service.request_return(session, actor.id, lease_id)
    return _run(handler)


@mcp.tool(description="归还健康复核：healthy=true 释放设备；false 整单隔离。")
def verify_return(lease_id: str, healthy: bool = True, ctx: Context = None) -> dict:
    def handler(session: Session) -> dict:
        actor = _actor(session, ctx)
        return lease_service.verify_return(session, actor.id, lease_id, healthy)
    return _run(handler)


@mcp.tool(description="对空闲环境发起维护。")
def start_maintenance(env_id: str, reason: str, ctx: Context = None) -> dict:
    def handler(session: Session) -> dict:
        actor = _actor(session, ctx)
        return maintenance_service.start(session, actor.id, {"envId": env_id, "reason": reason})
    return _run(handler)


@mcp.tool(description="维护健康复核：healthy=true 恢复可用；false 保持隔离。")
def verify_maintenance(record_id: str, healthy: bool = True, ctx: Context = None) -> dict:
    def handler(session: Session) -> dict:
        actor = _actor(session, ctx)
        return maintenance_service.verify(session, actor.id, record_id, healthy)
    return _run(handler)


@mcp.tool(description="工作空间概览：环境/设备计数、状态分布、待处理事项与最近动态。")
def get_snapshot_summary(ctx: Context = None) -> dict:
    def handler(session: Session) -> dict:
        _actor(session, ctx)
        snap = snapshot_service.build_snapshot(session)
        active = [e for e in snap["environments"] if e["lifecycle"] == "active"]
        return {
            "environments": {"total": len(active),
                             "available": sum(1 for e in active if e["status"] == "available"),
                             "occupied": sum(1 for e in active if e["status"] in ("leased", "running")),
                             "unavailable": sum(1 for e in active if e["status"] not in ("available", "leased", "running"))},
            "devices": len(snap["devices"]), "leases": len(snap["leases"]),
            "runs": len(snap["runs"]), "maintenance": len(snap["maintenance"]),
            "recentActivities": snap["activities"][:6],
        }
    return _run(handler)


@mcp.tool(description="查询资源事件流（审计）：可按实体类型/ID 过滤，倒序分页。")
def list_events(entity_type: str = "", entity_id: str = "", limit: int = 20,
                ctx: Context = None) -> list[dict]:
    def handler(session: Session) -> list[dict]:
        _actor(session, ctx)
        from .api import list_events as _impl
        return _impl(session=session, entity_type=entity_type, entity_id=entity_id,
                     limit=limit, before_id=0)["events"]
    return _run(handler)


@mcp.tool(description="复制流水线为独立 DAG 草稿，不触发任务。")
def copy_pipeline(pipeline_id: str, ctx: Context = None) -> dict:
    return _run(lambda session: pipeline_service.copy_pipeline(session, _actor(session, ctx).id, pipeline_id))


@mcp.tool(description="管理员校验并发布 DAG 配置；不占用设备、不触发 Jenkins。")
def publish_pipeline(pipeline_id: str, expected_revision: int, content_hash: str, ctx: Context = None) -> dict:
    return _run(lambda session: pipeline_service.publish_pipeline(session, _actor(session, ctx).id, pipeline_id,
        {'expectedRevision': expected_revision, 'contentHash': content_hash}))


@mcp.tool(description="持久调度 Jenkins DAG。提供唯一 request_key 防止重放；只使用服务端受控 Job。")
def start_pipeline_run(pipeline_version_id: str, env_id: str, request_key: str, ctx: Context = None) -> dict:
    return _run(lambda session: run_service.start_run(session, _actor(session, ctx).id,
        {'pipelineVersionId': pipeline_version_id, 'envId': env_id, 'requestKey': request_key, 'executionMode': 'jenkins'}))


@mcp.tool(description="启动演示执行任务：需要已发布流水线版本 ID 与匹配架构的空闲环境。")
def start_demo_run(pipeline_version_id: str, env_id: str, ctx: Context = None) -> dict:
    def handler(session: Session) -> dict:
        actor = _actor(session, ctx)
        return run_service.start_run(session, actor.id, {"pipelineVersionId": pipeline_version_id,
                                                         "envId": env_id})
    return _run(handler)


def mcp_app():
    """返回可挂载到 FastAPI 的 streamable HTTP ASGI 应用。

    streamable_http_path 指到根，使挂载点 /mcp 即真实端点（SDK 默认内部再带 /mcp 会变成 /mcp/mcp）。
    session manager 的启停由 app.main 的 lifespan 负责。
    """
    return mcp.streamable_http_app(streamable_http_path="/")
