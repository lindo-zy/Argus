"""开发种子：与前端 mock createSeed 相同的演示样本（24 环境 / 58 设备 / 演示状态）。

空库首次启动时自动注入；-reset 接口恢复到此样本。历史指标 fixture 仍由前端持有。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import settings
from .models import (
    Device, Environment, EnvironmentDevice, Lease, LeaseEnvironment, LeaseResource,
    MaintenanceRecord, PipelineDefinition, PipelineRun, PipelineVersion, ResourceEvent, User,
)
from .services import WORKSPACE_ID, ensure_workspace
from .services.pipelines import pipeline_xml

TZ = timezone(timedelta(hours=8))
BASELINE = datetime(2026, 10, 30, 14, 28, tzinfo=TZ)

USERS = [
    ("U-1001", "王磊", "admin", "active", "系统测试组 · 负责人", "2026-01-12", "argus-demo-admin"),
    ("U-1002", "张明", "member", "active", "系统测试组 · 测试工程师", "2026-02-20", "argus-token-zhangming"),
    ("U-1003", "李四", "member", "active", "系统测试组 · 测试工程师", "2026-03-05", "argus-token-lisi"),
    ("U-1004", "陈静", "member", "active", "质量组 · 测试工程师", "2026-06-18", "argus-token-chenjing"),
]

PIPELINES = [
    ("os-smoke-test", "操作系统核心冒烟", "v3", "published", "os-smoke-test", "x86_64",
     "安装、启动、网络及核心系统服务验证", "2026-10-29"),
    ("arm-compatibility", "ARM64 兼容性回归", "v2", "published", "arm-compatibility", "ARM64",
     "ARM64 平台硬件与系统兼容性验证", "2026-10-28"),
    ("storage-chaos", "存储故障注入", "v1", "draft", "storage-chaos", "ARM64",
     "受控故障场景与恢复能力验证", "2026-10-30"),
]

RUNS = [
    ("RUN-1028", "ENV-X86-02", "稳定性回归", "running", 68, 76, 112, 32, "#218", "李四", 3),
    ("RUN-1027", "ENV-VM-03", "核心冒烟", "success", 100, 112, 112, 18, "#217", "张明", 5),
    ("RUN-1026", None, "内核兼容性验证", "cleaning", 100, 43, 48, 26, "#216", "王磊", 4),
    ("RUN-1025", None, "长稳测试", "reconciling", 82, 39, 48, 48, "#215", "李四", 3),
    ("RUN-1024", "ENV-ARM-01", "ARM64 启动验证", "success", 100, 48, 48, 12, "#214", "张明", 5),
    ("RUN-1023", None, "驱动兼容回归", "running", 35, 20, 64, 14, "#213", "李四", 3),
    ("RUN-1022", None, "启动冒烟", "running", 76, 35, 48, 21, "#212", "张明", 3),
    ("RUN-1021", "ENV-VM-03", "虚拟机网络验证", "failed", 100, 61, 64, 16, "#211", "李四", 5),
]


def is_empty(session: Session) -> bool:
    return (session.execute(select(func.count()).select_from(Environment)).scalar_one() == 0)


def seed(session: Session) -> int:
    """注入演示样本，返回创建的环境数量。"""
    ensure_workspace(session)
    for uid, name, role, status, title, joined, token in USERS:
        session.add(User(id=uid, workspace_id=WORKSPACE_ID, name=name, role=role, status=status,
                         title=title, api_token=token,
                         created_at=datetime.fromisoformat(f"{joined}T09:00:00+08:00")))

    devices: list[Device] = []
    envs: list[Environment] = []
    env_device_map: dict[str, list[str]] = {}
    groups = [
        ["ENV-ARM-01", "ARM64 兼容性环境", "ARM64", "physical", 4, "available", "兼容性验证", "core"],
        ["ENV-X86-02", "x86 稳定性环境", "x86_64", "physical", 6, "running", "稳定性回归", "core"],
        ["ENV-VM-03", "虚拟化冒烟环境", "x86_64", "virtual", 3, "available", "核心冒烟", "smoke"],
        ["ENV-ST-04", "存储故障注入环境", "ARM64", "physical", 4, "maintenance", "故障注入", "storage"],
        ["ENV-ARM-05", "ARM64 性能基准环境", "ARM64", "physical", 3, "leased", "性能基准", "performance"],
        ["ENV-NET-06", "网络协议验证环境", "ARM64", "physical", 2, "available", "共享存储节点", "network"],
    ]
    names = ["安装升级", "内核回归", "安全验证", "驱动兼容", "容器集成", "可靠性", "文件系统",
             "桌面兼容", "启动冒烟", "虚拟化回归", "网络压力", "系统服务", "长稳验证", "镜像验证",
             "故障恢复", "调度性能", "内存管理", "基础冒烟"]
    states = ["available", "leased", "isolated", "running", "available", "available", "cleaning",
              "available", "running", "available", "maintenance", "available", "reconciling",
              "available", "leased", "available", "available", "leased"]
    for i, name in enumerate(names):
        arch = "x86_64" if i % 3 else "ARM64"
        type_ = "virtual" if i % 2 else "physical"
        groups.append([f"ENV-{i + 7:03d}", f"{name}环境", arch, type_, 2, states[i], name,
                       "smoke" if i % 2 else "core"])

    for idx, (env_id, name, arch, type_, count, status, description, suite) in enumerate(groups):
        device_ids = []
        for _ in range(count):
            device_id = f"DEV-{len(devices) + 1:03d}"
            device_ids.append(device_id)
            devices.append(Device(
                id=device_id, workspace_id=WORKSPACE_ID,
                name=f"{'arm' if arch == 'ARM64' else 'x86'}-{'vm' if type_ == 'virtual' else 'node'}-{len(devices) + 1:02d}",
                architecture=arch, resource_type=type_,
                cpu="Kunpeng 920 · 32 核" if arch == "ARM64" else "Xeon Gold · 24 核",
                memory="32 GB" if type_ == "virtual" else "128 GB",
                address=f"10.24.{idx + 1}.{len(devices) % 90 + 11}",
                host=f"host-{len(devices) % 4 + 1:02d}" if type_ == "virtual" else "",
                location="虚拟资源池 / 集群 A" if type_ == "virtual" else f"实验室 A / 机柜 {idx // 4 + 1}",
                lifecycle="active", operational_state="idle", health_state="unknown",
                health_checked_at=BASELINE, created_by="U-1001"))
        envs.append(Environment(
            id=env_id, workspace_id=WORKSPACE_ID, name=name, architecture=arch,
            resource_type=type_, description=description,
            os_version_text="OS 24.03", location="南京 · 系统测试实验室",
            tags=[arch, "物理机" if type_ == "physical" else "虚拟机"],
            owner_subject="王磊" if status == "leased" else "张明",
            lifecycle="active", created_by="U-1001"))
        env_device_map[env_id] = device_ids

    # 共享设备：ENV-NET-06 复用 ENV-ST-04 的第一台设备（一台设备不可重复借用的演示）。
    shared = env_device_map["ENV-ST-04"][0]
    if shared not in env_device_map["ENV-NET-06"]:
        env_device_map["ENV-NET-06"].append(shared)

    for env in envs:
        session.add(env)
        for device_id in env_device_map[env.id]:
            session.add(EnvironmentDevice(workspace_id=WORKSPACE_ID, environment_id=env.id,
                                          device_id=device_id, created_by="U-1001"))
    for device in devices:
        session.add(device)

    status_of = {g[0]: g[5] for g in groups}

    # 手工借用单：首设备为 leased 的环境（与 mock 一致，两张整单）。
    lease_seq = 1001
    device_row = {d.id: d for d in devices}
    for env in envs:
        if status_of[env.id] != "leased":
            continue
        lease_id = f"LEASE-{lease_seq}"
        lease_seq += 1
        start = datetime(2026, 10, 30, 9, 0, tzinfo=TZ)
        lease = Lease(id=lease_id, workspace_id=WORKSPACE_ID, kind="manual",
                      holder_subject="U-1001", purpose="性能基线复测与数据采集" if lease_seq == 1002 else "版本功能验证",
                      status="active", start_at=start, expires_at=start + timedelta(hours=24),
                      hours=24)
        session.add(lease)
        session.add(LeaseEnvironment(workspace_id=WORKSPACE_ID, lease_id=lease_id,
                                     environment_id=env.id, name_snapshot=env.name,
                                     topology_version=1,
                                     device_ids_snapshot=env_device_map[env.id]))
        for device_id in env_device_map[env.id]:
            session.add(LeaseResource(workspace_id=WORKSPACE_ID, lease_id=lease_id,
                                      device_id=device_id, active=True,
                                      device_snapshot={"name": device_row[device_id].name,
                                                       "holder": "王磊"}))

    # 维护记录：ENV-ST-04 维护中、ENV-017 维护中、ENV-009 隔离（与 mock MT-003/002/001 一致）。
    mt_defs = [
        ("MT-003", "ENV-ST-04", "共享存储节点磁盘健康异常，替换介质后复核", "maintenance", "U-1003", "RDC-2086"),
        ("MT-002", "ENV-017", "网络接口固件升级", "maintenance", "U-1002", "RDC-2080"),
        ("MT-001", "ENV-009", "设备失联，等待人工健康复核", "isolated", "U-1001", "RDC-2072"),
    ]
    device_status_map: dict[str, str] = {}
    for mt_id, env_id, reason, status, creator, issue in mt_defs:
        session.add(MaintenanceRecord(
            id=mt_id, workspace_id=WORKSPACE_ID, environment_ids=[env_id],
            device_ids=env_device_map[env_id], reason=reason, status=status,
            result="unhealthy" if status == "isolated" else "",
            issue_ref=issue, created_by=creator,
            created_at=BASELINE - timedelta(hours=6)))
        op = "isolated" if status == "isolated" else "maintenance"
        for device_id in env_device_map[env_id]:
            device_row[device_id].operational_state = op

    # 流水线定义与版本（先于运行，满足外键）。
    for pid, name, version, status, job, arch, desc, date in PIPELINES:
        session.add(PipelineDefinition(id=pid, workspace_id=WORKSPACE_ID, name=name,
                                       description=desc, connection_ref="jenkins-main",
                                       job_ref=job, architecture=arch))
        session.add(PipelineVersion(
            id=f"{pid}@{version}", workspace_id=WORKSPACE_ID, definition_id=pid, version=version,
            xml=pipeline_xml(pid, name, version.removeprefix("v"), job, arch), status=status,
            published_by="U-1001" if status == "published" else "",
            published_at=datetime.fromisoformat(f"{date}T10:00:00+08:00") if status == "published" else None))
    session.flush()

    # 演示执行：8 条运行；非终态运行建 run 租约并占设备。
    env_by_id = {e.id: e for e in envs}
    run_offsets = {"RUN-1026": "ENV-013", "RUN-1025": "ENV-019",
                   "RUN-1023": "ENV-015", "RUN-1022": "ENV-020"}
    for offset, (run_id, env_id, name, status, progress, passed, total, duration, build, user, evidence) in enumerate(RUNS):
        if env_id is None:
            env_id = run_offsets[run_id]
        started = BASELINE - timedelta(hours=offset + 2)
        started_by = "U-1003" if user == "李四" else "U-1002" if user == "张明" else "U-1001"
        run_lease = None
        if status in ("running", "cleaning", "reconciling"):
            run_lease = Lease(id=f"LEASE-R-{run_id}", workspace_id=WORKSPACE_ID, kind="run",
                              holder_subject=started_by, purpose=f"{name} 演示执行",
                              status="active", start_at=started,
                              expires_at=started + timedelta(hours=8), hours=8)
            session.add(run_lease)
            session.add(LeaseEnvironment(workspace_id=WORKSPACE_ID, lease_id=run_lease.id,
                                         environment_id=env_id, name_snapshot=env_by_id[env_id].name,
                                         topology_version=1,
                                         device_ids_snapshot=env_device_map[env_id]))
            for device_id in env_device_map[env_id]:
                session.add(LeaseResource(workspace_id=WORKSPACE_ID, lease_id=run_lease.id,
                                          device_id=device_id, active=True,
                                          device_snapshot={"name": device_row[device_id].name}))
        run = PipelineRun(
            id=run_id, workspace_id=WORKSPACE_ID, pipeline_version_id="os-smoke-test@v3",
            lease_id=run_lease.id if run_lease is not None else None,
            environment_id=env_id, name=name, status=status,
            result="success" if status == "success" else "failed" if status == "failed" else "",
            progress=progress, passed=passed, total=total, duration_minutes=duration,
            jenkins_build=build, os_version_text="OS 24.03 RC2",
            started_by=started_by,
            started_at=started, ended_at=started + timedelta(minutes=duration) if status in ("success", "failed") else None,
            evidence_count=evidence,
            cleanup_state="done" if status in ("success", "failed") else "pending",
            image_ref=f"os-24.03-rc2-{env_by_id[env_id].architecture.lower()}.img")
        session.add(run)

    # 演示事件起点。
    session.add(ResourceEvent(workspace_id=WORKSPACE_ID, actor_subject="U-1001",
                              action="seed", entity_type="workspace", entity_id=WORKSPACE_ID,
                              payload={"text": "已注入初始演示样本"}, tone="blue"))
    session.commit()
    return len(envs)


def seed_if_empty(session: Session) -> bool:
    if not settings().seed_on_empty or not is_empty(session):
        return False
    seed(session)
    return True
