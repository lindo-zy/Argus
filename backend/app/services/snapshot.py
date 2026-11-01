"""快照组装：与前端 Mock 同构的全量工作空间状态。

resource_events 同时充当「最近动态」来源（activities 仅是展示裁剪，不是审计边界）。
issues 为 RDC 接入前的固定演示样本，不入库。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    Device, Environment, Lease, MaintenanceRecord, PipelineDefinition, PipelineRun,
    PipelineVersion, ResourceEvent, User,
)
from ..state import device_occupancy
from . import WORKSPACE_ID, revision_of
from .devices import device_dict
from .environments import environment_dict
from .leases import lease_dict_of
from .maintenance import maintenance_dict
from .pipelines import pipeline_xml
from .runs import run_dict_of

ISSUES_FIXTURE = [
    {"id": "RDC-2086", "title": "存储节点 I/O 超时", "severity": "P1", "module": "存储", "owner": "李四", "status": "pending"},
    {"id": "RDC-2080", "title": "网卡固件升级后链路抖动", "severity": "P2", "module": "网络", "owner": "张明", "status": "pending"},
    {"id": "RDC-2072", "title": "安全验证节点偶发失联", "severity": "P2", "module": "基础设施", "owner": "王磊", "status": "pending"},
    {"id": "RDC-2065", "title": "虚拟机 DNS 解析超时", "severity": "P2", "module": "网络", "owner": "李四", "status": "success"},
]

PIPELINE_FIXTURE = [
    ("os-smoke-test", "操作系统核心冒烟", "v3", "published", "os-smoke-test", "x86_64", "2026-10-29", "安装、启动、网络及核心系统服务验证"),
    ("arm-compatibility", "ARM64 兼容性回归", "v2", "published", "arm-compatibility", "ARM64", "2026-10-28", "ARM64 平台硬件与系统兼容性验证"),
    ("storage-chaos", "存储故障注入", "v1", "draft", "storage-chaos", "ARM64", "2026-10-30", "受控故障场景与恢复能力验证"),
]


def build_snapshot(session: Session) -> dict:
    occupancy = device_occupancy(session, WORKSPACE_ID)
    devices = session.execute(select(Device).order_by(Device.id)).scalars().all()
    environments = session.execute(select(Environment).order_by(Environment.id)).scalars().all()
    leases = session.execute(select(Lease).order_by(Lease.id)).scalars().all()
    runs = session.execute(select(PipelineRun).order_by(PipelineRun.id)).scalars().all()
    records = session.execute(select(MaintenanceRecord).order_by(MaintenanceRecord.id)).scalars().all()
    users = session.execute(select(User).order_by(User.id)).scalars().all()
    events = session.execute(
        select(ResourceEvent).order_by(ResourceEvent.id.desc()).limit(12)
    ).scalars().all()

    return {
        "schema": 3,
        "revision": revision_of(session),
        "users": [{"id": u.id, "name": u.name, "role": u.role, "status": u.status,
                   "title": u.title, "joined": u.created_at.date().isoformat()} for u in users],
        "currentUserId": None,  # 前端按请求身份注入
        "devices": [device_dict(session, d, occupancy) for d in devices],
        "environments": [environment_dict(session, e, occupancy) for e in environments],
        "leases": [lease_dict_of(session, l) for l in leases],
        "runs": [run_dict_of(session, r) for r in runs],
        "maintenance": [maintenance_dict(session, r) for r in records],
        "pipelines": pipeline_dicts(session),
        "issues": [dict(i) for i in ISSUES_FIXTURE],
        "activities": [{"time": e.occurred_at.astimezone().strftime("%H:%M"),
                        "text": (e.payload or {}).get("text", f"{e.action} {e.entity_id}"),
                        "tone": e.tone} for e in events],
    }


def pipeline_dicts(session: Session) -> list[dict]:
    definitions = session.execute(select(PipelineDefinition)).scalars().all()
    versions = session.execute(select(PipelineVersion)).scalars().all()
    if not definitions:
        # 空库（未 seed）时返回固定演示样本，保持界面可用。
        return [{"id": pid, "name": name, "version": ver, "status": status, "job": job,
                 "arch": arch, "date": date, "description": desc,
                 "xml": pipeline_xml(pid, name, ver.removeprefix("v"), job, arch)}
                for pid, name, ver, status, job, arch, date, desc in PIPELINE_FIXTURE]
    latest: dict[str, PipelineVersion] = {}
    for v in versions:
        current = latest.get(v.definition_id)
        if current is None or v.version > current.version:
            latest[v.definition_id] = v
    result = []
    for d in definitions:
        v = latest.get(d.id)
        version_label = v.version if v else "v1"
        # 已发布版本内容不可变；旧 seed 只写入占位 stub，展示层回退到契约形配置，
        # 不因此改写库中的版本内容。
        xml = (v.xml or "").strip() if v else ""
        if "<jenkins " not in xml:
            xml = pipeline_xml(d.id, d.name, version_label.removeprefix("v"),
                               d.job_ref, d.architecture)
        result.append({
            "id": d.id, "name": d.name, "version": version_label,
            "status": v.status if v else "draft", "job": d.job_ref, "arch": d.architecture,
            "date": (v.published_at or v.__dict__.get("created_at")).date().isoformat()
                    if v and (v.published_at or getattr(v, "created_at", None)) else "",
            "description": d.description,
            "xml": xml,
            "pipelineVersionId": v.id if v else None,
        })
    return result
