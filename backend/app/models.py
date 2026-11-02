"""ORM 模型：users + docs/07 的 12 张表。

占用建模：设备表只存资产与维护/隔离事实（operational_state/health_state），
借用、执行、复核等占用一律由 lease_resources.active 与 pipeline_runs 派生。
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now(), onupdate=utcnow)


class Workspace(Base):
    """工作空间：本轮单默认工作空间，字段按生产口径预留。"""

    __tablename__ = "workspaces"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))


class User(Base):
    """演示身份投影：真实身份源接入前用于权限与操作者记录。"""

    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(64), ForeignKey("workspaces.id"), index=True)
    name: Mapped[str] = mapped_column(String(60), unique=True)
    role: Mapped[str] = mapped_column(String(16), default="member")  # admin | member
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | disabled
    title: Mapped[str] = mapped_column(String(120), default="")
    api_token: Mapped[str] = mapped_column(String(128), unique=True, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())


class Environment(Base, TimestampMixin):
    __tablename__ = "environments"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(64), ForeignKey("workspaces.id"), index=True)
    name: Mapped[str] = mapped_column(String(60))
    architecture: Mapped[str] = mapped_column(String(16))  # ARM64 | x86_64
    resource_type: Mapped[str] = mapped_column(String(16))  # physical | virtual
    description: Mapped[str] = mapped_column(String(200), default="")
    os_version_text: Mapped[str] = mapped_column(String(80), default="")  # 可选文本，无 os_versions 表
    location: Mapped[str] = mapped_column(String(100), default="")
    tags: Mapped[list] = mapped_column(JSONB, default=list)
    owner_subject: Mapped[str] = mapped_column(String(64), default="")
    lifecycle: Mapped[str] = mapped_column(String(16), default="active", index=True)  # active | retired | template
    topology_version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(String(64), default="")
    row_version: Mapped[int] = mapped_column(Integer, default=1)

    devices: Mapped[list["Device"]] = relationship(
        secondary="environment_devices", lazy="selectin", viewonly=True,
    )


class Device(Base, TimestampMixin):
    __tablename__ = "devices"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(64), ForeignKey("workspaces.id"), index=True)
    name: Mapped[str] = mapped_column(String(60))
    architecture: Mapped[str] = mapped_column(String(16))
    resource_type: Mapped[str] = mapped_column(String(16))
    cpu: Mapped[str] = mapped_column(String(100), default="")
    memory: Mapped[str] = mapped_column(String(60), default="")
    address: Mapped[str] = mapped_column(String(100), default="")  # 纯文本，不做探测
    host: Mapped[str] = mapped_column(String(100), default="")  # 虚拟机所在宿主机，物理机留空
    location: Mapped[str] = mapped_column(String(100), default="")
    lifecycle: Mapped[str] = mapped_column(String(16), default="active", index=True)  # active | retired
    operational_state: Mapped[str] = mapped_column(String(16), default="idle")  # idle | maintenance | isolated
    health_state: Mapped[str] = mapped_column(String(16), default="unknown")  # healthy | unhealthy | unknown
    health_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), default="")
    row_version: Mapped[int] = mapped_column(Integer, default=1)

    environments: Mapped[list["Environment"]] = relationship(
        secondary="environment_devices", lazy="selectin", viewonly=True,
    )


class EnvironmentDevice(Base):
    """当前多对多拓扑；历史范围由租约快照保留，不在此追溯。"""

    __tablename__ = "environment_devices"
    workspace_id: Mapped[str] = mapped_column(String(64), ForeignKey("workspaces.id"), primary_key=True)
    environment_id: Mapped[str] = mapped_column(String(64), ForeignKey("environments.id"), primary_key=True)
    device_id: Mapped[str] = mapped_column(String(64), ForeignKey("devices.id"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())
    created_by: Mapped[str] = mapped_column(String(64), default="")
    __table_args__ = (Index("ix_env_devices_device", "device_id"),)


class Lease(Base):
    """实际占用（kind=manual/run/maintenance）；到期不是释放依据。"""

    __tablename__ = "leases"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(64), ForeignKey("workspaces.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16), default="manual")  # manual | run | maintenance
    holder_subject: Mapped[str] = mapped_column(String(64), index=True)
    purpose: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)  # active | checking | released | isolated
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    hours: Mapped[int] = mapped_column(Integer, default=4)
    request_key: Mapped[str] = mapped_column(String(128), default="")
    request_hash: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())
    __table_args__ = (Index("ix_leases_holder_status", "holder_subject", "status", "expires_at"),)


class LeaseEnvironment(Base):
    """成功环境集合快照：不随后续拓扑调整重算历史。"""

    __tablename__ = "lease_environments"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(64), ForeignKey("workspaces.id"))
    lease_id: Mapped[str] = mapped_column(String(64), ForeignKey("leases.id"), index=True)
    environment_id: Mapped[str] = mapped_column(String(64), ForeignKey("environments.id"))
    name_snapshot: Mapped[str] = mapped_column(String(60))
    topology_version: Mapped[int] = mapped_column(Integer)
    device_ids_snapshot: Mapped[list] = mapped_column(JSONB, default=list)
    __table_args__ = (UniqueConstraint("lease_id", "environment_id", name="uq_lease_env"),)


class LeaseResource(Base):
    """设备级权威占用：active 行 device_id 唯一（部分索引）兜底互斥。"""

    __tablename__ = "lease_resources"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(64), ForeignKey("workspaces.id"))
    lease_id: Mapped[str] = mapped_column(String(64), ForeignKey("leases.id"), index=True)
    device_id: Mapped[str] = mapped_column(String(64), ForeignKey("devices.id"), index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    device_snapshot: Mapped[dict] = mapped_column(JSONB, default=dict)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (
        Index("uq_lease_resource_active_device", "workspace_id", "device_id", unique=True,
              postgresql_where=text("active")),
        UniqueConstraint("lease_id", "device_id", name="uq_lease_resource"),
    )


class MaintenanceRecord(Base):
    __tablename__ = "maintenance_records"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(64), ForeignKey("workspaces.id"), index=True)
    lease_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("leases.id"), nullable=True)
    source_lease_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    environment_ids: Mapped[list] = mapped_column(JSONB, default=list)
    device_ids: Mapped[list] = mapped_column(JSONB, default=list)
    reason: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(16), default="maintenance", index=True)  # maintenance | isolated | success
    result: Mapped[str] = mapped_column(String(16), default="")  # healthy | unhealthy | ""
    issue_ref: Mapped[str] = mapped_column(String(64), default="待关联")
    verification_evidence: Mapped[dict] = mapped_column(JSONB, default=dict)
    verified_by: Mapped[str] = mapped_column(String(64), default="")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())


class ResourceEvent(Base):
    """审计与幂等：批量借用结果以 (workspace, actor, request_key) 唯一。"""

    __tablename__ = "resource_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(64), ForeignKey("workspaces.id"), index=True)
    actor_subject: Mapped[str] = mapped_column(String(64), index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    entity_type: Mapped[str] = mapped_column(String(32), index=True)
    entity_id: Mapped[str] = mapped_column(String(64), index=True)
    request_key: Mapped[str] = mapped_column(String(128), default="")
    request_hash: Mapped[str] = mapped_column(String(128), default="")
    result_snapshot: Mapped[dict] = mapped_column(JSONB, default=dict)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    tone: Mapped[str] = mapped_column(String(16), default="blue")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())
    __table_args__ = (
        Index("ix_events_entity_time", "entity_type", "entity_id", "occurred_at"),
        Index("uq_events_request_key", "workspace_id", "actor_subject", "request_key", unique=True,
              postgresql_where=text("request_key <> ''")),
        Index("ix_events_time", "occurred_at"),
    )


class PipelineDefinition(Base):
    __tablename__ = "pipeline_definitions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(64), ForeignKey("workspaces.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String(200), default="")
    connection_ref: Mapped[str] = mapped_column(String(64), default="jenkins-main")
    job_ref: Mapped[str] = mapped_column(String(120), default="")
    architecture: Mapped[str] = mapped_column(String(16), default="x86_64")
    # 动态编排同步状态：'' | pending | synced | failed；job_synced_hash 为最近一次同步的配置摘要。
    job_sync_state: Mapped[str] = mapped_column(String(16), default="")
    job_synced_hash: Mapped[str] = mapped_column(String(64), default="")


class PipelineVersion(Base):
    """发布版本不可变；content_hash 固化内容。发布治理为后续独立设计。"""

    __tablename__ = "pipeline_versions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(64), ForeignKey("workspaces.id"), index=True)
    definition_id: Mapped[str] = mapped_column(String(64), ForeignKey("pipeline_definitions.id"))
    version: Mapped[str] = mapped_column(String(32))
    xml: Mapped[str] = mapped_column(Text, default="")
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft | published
    published_by: Mapped[str] = mapped_column(String(64), default="")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (UniqueConstraint("definition_id", "version", name="uq_pipeline_version"),)


class PipelineRun(Base):
    """运行固化配置、参数、制品与外部 ID；run 不依赖当前资产配置。本轮执行为演示模拟。"""

    __tablename__ = "pipeline_runs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(64), ForeignKey("workspaces.id"), index=True)
    pipeline_version_id: Mapped[str] = mapped_column(String(64), ForeignKey("pipeline_versions.id"))
    lease_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("leases.id"), nullable=True)
    environment_id: Mapped[str] = mapped_column(String(64), ForeignKey("environments.id"), index=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    params_snapshot: Mapped[dict] = mapped_column(JSONB, default=dict)
    image_ref: Mapped[str] = mapped_column(String(200), default="")
    image_digest: Mapped[str] = mapped_column(String(128), default="")
    os_version_text: Mapped[str] = mapped_column(String(80), default="")
    jenkins_job: Mapped[str] = mapped_column(String(120), default="#mock")
    jenkins_queue: Mapped[str] = mapped_column(String(64), default="")
    jenkins_build: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(16), default="running", index=True)
    result: Mapped[str] = mapped_column(String(16), default="")  # success | failed | ""
    progress: Mapped[int] = mapped_column(Integer, default=12)
    passed: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=48)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=0)
    evidence_count: Mapped[int] = mapped_column(Integer, default=1)
    cleanup_state: Mapped[str] = mapped_column(String(16), default="")  # pending | done | isolated
    evidence_refs: Mapped[dict] = mapped_column(JSONB, default=dict)
    started_by: Mapped[str] = mapped_column(String(64), default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PipelineExecution(Base):
    """流水线执行的外部编排意图：先持久化再提交，每次运行至多提交一次。

    request_key/request_hash 支撑幂等重放；snapshot 固化 Job 名、参数与配置摘要，
    ARGUS_TOKEN 用于 Jenkins queue/build 对账（提交结果不明时按 token 找回，不重复提交）。
    JSON 字段整体替换、不原地修改，保证每个状态转换都被 SQLAlchemy 持久化。
    """
    __tablename__ = "pipeline_executions"
    run_id: Mapped[str] = mapped_column(String(64), ForeignKey("pipeline_runs.id"), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    request_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSONB, default=dict)
    nodes: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())
    __table_args__ = (UniqueConstraint("actor_id", "request_key", name="uq_execution_request"),)


class BackgroundTask(Base):
    """可恢复任务登记（提交/轮询/对账/清理/重试）；持久协调器为 Jenkins 接入阶段实现。"""

    __tablename__ = "background_tasks"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(64), ForeignKey("workspaces.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    owner: Mapped[str] = mapped_column(String(64), default="")
    claim_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dedupe_key: Mapped[str] = mapped_column(String(128), default="")
    last_error: Mapped[str] = mapped_column(Text, default="")
