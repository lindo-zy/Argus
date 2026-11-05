"""运行入口、旧样本兼容与人工清理复核。

三条执行路径：
- payload 带 executionMode=jenkins + requestKey → orchestration 节点级持久编排（受控固定 Job）；
- 已发布 DAG 版本（schema v2）→ 动态流水线：先持久化执行意图再一次性提交动态 Job，
  queue→build→终态逐段回同步，终态自动清理释放；Jenkins 未配置时退回演示模拟；
- 其余（旧样本 XML）→ 演示模拟。
"""
from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..errors import BusinessError
from ..models import (
    Device, Environment, Lease, LeaseResource, PipelineDefinition, PipelineExecution,
    PipelineRun, PipelineVersion, User,
)
from ..state import device_occupancy, env_device_ids
from . import WORKSPACE_ID, get_user, next_id, now, record_event
from .jenkins import JenkinsClient, JenkinsError, connection_config, job_name_for
from .leases import _borrow_failure
from .pipelines import draft_content
from .resource_locks import lock_resources
from .maintenance import create_isolation_record


def jenkins_client() -> JenkinsClient | None:
    """Jenkins 未配置（或配置无效）时返回 None → 执行退回演示模拟。"""
    try:
        config = connection_config()
    except BusinessError as exc:
        if exc.code == 'jenkins_unconfigured':
            return None
        raise
    return JenkinsClient(config)


def _runtime_graph(version: PipelineVersion) -> dict:
    content = draft_content(version)
    graph = content.get("graph")
    if not graph or not graph.get("nodes"):
        raise BusinessError("该版本为旧版声明式配置，请先在编排中保存为 DAG 版本后再启动。")
    return graph


def start_run(session: Session, actor_id: str, payload: dict) -> dict:
    if payload.get('executionMode') == 'jenkins' or payload.get('requestKey'):
        from .orchestration import start_execution
        return start_execution(session, actor_id, payload)
    # Preserve legacy clients for sample XML runs; DAG execution always uses Jenkins.
    candidate = session.get(PipelineVersion, payload.get('pipelineVersionId') or '')
    if not candidate and payload.get('pipelineId'):
        candidate = session.execute(select(PipelineVersion).where(
            PipelineVersion.definition_id == payload['pipelineId'], PipelineVersion.schema_version == 2)).scalars().first()
    # v2 DAG 版本继续走下方动态流水线路径（Jenkins 未配置时退回演示模拟）；
    # 旧版 XML 样本保持演示模拟兼容。
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
                  kind="run", holder_subject=actor.id, purpose=f"{definition.name} 执行",
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
    run_id = next_id(session, PipelineRun, "RUN")
    client = jenkins_client()
    if client is None:
        # 演示模拟：与既有行为一致，不触碰 Jenkins。
        run = PipelineRun(
            id=run_id, workspace_id=WORKSPACE_ID,
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
    graph = _runtime_graph(version)
    install_nodes = [n for n in graph["nodes"] if n["type"] == "install"]
    if {n["config"].get("envId", "") for n in install_nodes} != {env.id}:
        raise BusinessError("编排中的环境安装节点与所选执行环境不一致，请更新编排或选择匹配环境。")
    image_ref = install_nodes[0]["config"].get("imageRef", "")
    cases = sorted({ref["id"] for n in graph["nodes"] if n["type"] == "cases"
                    for ref in n["config"].get("caseRefs", [])})
    token = uuid4().hex
    params = {"IMAGE_REF": image_ref, "TEST_SUITE": "smoke", "ENV_ID": env.id,
              "CASES": json.dumps(cases, ensure_ascii=False), "ARGUS_RUN_ID": run_id,
              "ARGUS_TOKEN": token, "ARGUS_CONTENT_HASH": version.content_hash}
    job = job_name_for(settings().jenkins_job_prefix, definition.id)
    run = PipelineRun(
        id=run_id, workspace_id=WORKSPACE_ID,
        pipeline_version_id=version.id, lease_id=lease.id, environment_id=env.id,
        name=definition.name, params_snapshot={"IMAGE_REF": image_ref, "TEST_SUITE": "smoke"},
        image_ref=image_ref, os_version_text=env.os_version_text or "OS 24.03",
        jenkins_job=job, jenkins_queue="", jenkins_build="",
        status="reconciling", started_by=actor.id, cleanup_state="pending", evidence_refs={})
    session.add(run)
    session.flush()
    session.add(PipelineExecution(
        run_id=run_id, actor_id=actor.id, request_key=f"run:{run_id}",
        request_hash=hashlib.sha256(
            json.dumps(params, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
        snapshot={"job": job, "params": params, "contentHash": version.content_hash,
                  "caseCount": len(cases), "submitted": False, "queue": "", "build": ""},
        nodes=[{"id": n["id"], "type": n["type"], "name": n["name"]} for n in graph["nodes"]]))
    session.flush()
    record_event(session, actor_id=actor.id, action="run_activity", entity_type="run",
                 entity_id=run_id, payload={"text": f"{run_id} 已编排 {definition.name}，等待调度"})
    # 意图先落库再提交外部任务：此处先提交事务，提交结果不明时可按 ARGUS_TOKEN 对账。
    session.commit()
    _dispatch(session, run, client)
    record_event(session, actor_id=actor.id, action="run_activity", entity_type="run",
                 entity_id=run.id, payload={"text": f"{run.id} 已启动 {run.name}"})
    session.flush()
    return run_dict_of(session, run)


def _dispatch(session: Session, run: PipelineRun, client: JenkinsClient) -> None:
    """同步动态 Job 并一次性提交构建；结果不明时保持 reconciling 交由对账，绝不重提。"""
    execution = session.get(PipelineExecution, run.id)
    snapshot = execution.snapshot
    if snapshot.get("submitted"):
        return
    snapshot = dict(snapshot)
    snapshot["submitted"] = True
    execution.snapshot = snapshot  # JSON 整体替换，保证状态转换被持久化
    session.flush()
    session.commit()  # 「已提交」标记先于网络调用落库
    try:
        version = session.get(PipelineVersion, run.pipeline_version_id)
        definition = session.get(PipelineDefinition, version.definition_id)
        from .provisioning import sync_pipeline_job
        sync_pipeline_job(session, run.started_by, definition, version)
        result = client.submit_dynamic(snapshot["job"], snapshot["params"])
    except (JenkinsError, BusinessError):
        run.status = "reconciling"
        session.flush()
        return
    snapshot = dict(execution.snapshot)
    snapshot.update({"queue": str(result["queue"])})
    execution.snapshot = snapshot
    run.jenkins_queue = str(result["queue"])
    run.status = "running"
    session.flush()


def reconcile_run(session: Session, run: PipelineRun) -> dict:
    """单运行对账：queue → build → 终态；终态自动清理释放。仅处理有执行意图的运行。"""
    execution = session.get(PipelineExecution, run.id)
    if execution is None:
        return run_dict_of(session, run)
    client = jenkins_client()
    if client is None:
        return run_dict_of(session, run)
    snapshot = execution.snapshot
    if not snapshot.get("submitted"):
        _dispatch(session, run, client)
        return run_dict_of(session, run)
    token = snapshot["params"]["ARGUS_TOKEN"]
    try:
        outcome = client.poll_run(snapshot["job"], token,
                                  queue_id=run.jenkins_queue or snapshot.get("queue", ""),
                                  build=run.jenkins_build or snapshot.get("build", ""))
    except JenkinsError:
        run.status = "reconciling"
        session.flush()
        return run_dict_of(session, run)
    if outcome.get("build") and outcome["build"] != run.jenkins_build:
        run.jenkins_build = outcome["build"]
        snapshot = dict(snapshot)
        snapshot["build"] = outcome["build"]
        execution.snapshot = snapshot
    if outcome["status"] == "queued":
        run.status = "running"
        run.progress = max(run.progress, 10)
    elif outcome["status"] == "running":
        run.status = "running"
        run.progress = max(run.progress, 50)
    elif outcome["status"] in ("success", "failed"):
        _complete_run(session, run, execution, outcome)
    session.flush()
    return run_dict_of(session, run)


def _complete_run(session: Session, run: PipelineRun, execution: PipelineExecution, outcome: dict) -> None:
    """Jenkins 终态 → 运行终态 → 自动清理释放（复用既有清理语义，复核视为通过）。"""
    success = outcome["status"] == "success"
    total = int(execution.snapshot.get("caseCount") or 0)
    run.status = "cleaning"
    run.result = "success" if success else "failed"
    run.progress = 100
    run.total = total
    run.passed = total if success else 0
    run.evidence_count = 1
    run.evidence_refs = {"jenkinsConsole": outcome.get("buildUrl", ""),
                         "jenkinsResult": outcome.get("result", "")}
    run.ended_at = now()
    duration = (run.ended_at - run.started_at).total_seconds() / 60
    run.duration_minutes = max(1, round(duration))
    record_event(session, actor_id=run.started_by, action="run_activity", entity_type="run",
                 entity_id=run.id, tone="amber",
                 payload={"text": f"{run.id} Jenkins 构建终态 {outcome.get('result', '')}，进入清理"})
    session.flush()
    try:
        cleanup_run(session, run.started_by, run.id, healthy=True)
    except BusinessError:
        # 并发对账（worker 与手动入口）下另一个会话已完成清理；本会话状态以数据库为准。
        session.rollback()


def reconcile_active_runs(session: Session) -> dict:
    """批量对账所有运行中/对账中的运行（REST/worker 共用）。每个运行独立提交，互不拖累。"""
    runs = session.execute(
        select(PipelineRun).where(PipelineRun.status.in_(("running", "reconciling")))).scalars().all()
    checked, finished = 0, []
    for run in runs:
        checked += 1
        reconcile_run(session, run)
        if run.status in ("success", "failed"):
            finished.append(run.id)
        session.commit()
    return {"checked": checked, "finished": finished}


def finish_run(session: Session, actor_id: str, run_id: str, success: bool = True) -> dict:
    actor = get_user(session, actor_id)
    run = session.get(PipelineRun, run_id)
    if run and session.get(PipelineExecution, run_id):
        raise BusinessError('Jenkins 运行只能由外部任务对账确认终态，不能模拟结束。')
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
    if type(healthy) is not bool:
        raise BusinessError('请明确提供清理复核结论。')
    execution = session.get(PipelineExecution, run.id)
    if execution and actor.id != run.started_by and actor.role != 'admin':
        raise BusinessError('只有执行人或管理员可以记录该运行的清理复核。')
    snapshot = execution.snapshot if execution else {}
    env_ids = [e['id'] for e in snapshot.get('environments') or []] or [run.environment_id]
    device_ids_snapshot = [d['id'] for d in snapshot.get('devices') or []]
    lock_resources(session, env_ids, device_ids_snapshot)
    run = session.execute(select(PipelineRun).where(PipelineRun.id == run_id).with_for_update()
                          .execution_options(populate_existing=True)).scalar_one()
    if run.status != 'cleaning':
        raise BusinessError('任务已完成清理，请刷新。')
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
        create_isolation_record(session, actor_id=actor.id, environment_ids=env_ids,
                                device_ids=device_ids, reason=f"{run.id} 清理异常，设备已隔离")
    execution = session.get(PipelineExecution, run.id)
    if run.lease_id:
        lease = session.get(Lease, run.lease_id)
        lease.status = 'released' if healthy else 'isolated'
    if not execution:
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
    execution = session.get(PipelineExecution, run.id)
    snapshot = execution.snapshot if execution else {}
    return {
        # 动态流水线（无 connection 键）与节点编排（connection.kind）统一对外展示为 jenkins。
        'executionMode': (snapshot.get('connection') or {}).get('kind', 'jenkins') if execution else 'demo',
        'nodes': [{k: v for k, v in n.items() if k not in ('params', 'token')} for n in execution.nodes] if execution else [],
        'environmentSnapshots': snapshot.get('environments') or [],
        'contentHash': snapshot.get('contentHash', ''),
        'evidenceRefs': run.evidence_refs,
        "id": run.id, "envId": run.environment_id, "name": run.name,
        "status": run.status, "progress": run.progress, "passed": run.passed,
        "total": run.total, "duration": run.duration_minutes, "build": run.jenkins_build,
        "result": run.result, "version": run.os_version_text,
        "user": starter.name if starter else run.started_by,
        "started": run.started_at.isoformat(), "artifact": run.image_ref,
        "config": f"{definition.id if definition else '?'}@{version.version if version else '?'}",
        "evidence": run.evidence_count, "cleanupState": run.cleanup_state,
        "jobRef": run.jenkins_job, "queueId": run.jenkins_queue,
        "consoleUrl": (run.evidence_refs or {}).get("jenkinsConsole", ""),
    }
