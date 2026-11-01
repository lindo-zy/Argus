"""维护域与演示执行域测试：维护启动/复核、执行状态机、清理释放/隔离。

规则来源 docs/02「归还、维护与执行」，对齐 frontend/tests/multi-borrow.test.js。
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import (
    Device, Lease, LeaseResource, MaintenanceRecord, PipelineRun, ResourceEvent,
)
from app.services import leases as lease_service
from app.services import maintenance as maintenance_service
from app.services import runs as run_service
from conftest import expect_business_error, env_status

ADMIN = "U-1001"
MEMBER = "U-1003"

ARM_CONFIG = "arm-compatibility@v2"    # published, ARM64
X86_CONFIG = "os-smoke-test@v3"        # published, x86_64
DRAFT_CONFIG = "storage-chaos@v1"      # draft


def start(session, actor=ADMIN, env_id="ENV-ARM-01", reason="固件升级窗口", pipeline=X86_CONFIG):
    """pipeline 含 @ 时按 pipelineVersionId 精确指定，否则按 definition id 解析已发布版本。"""
    key = "pipelineVersionId" if "@" in pipeline else "pipelineId"
    return run_service.start_run(session, actor, {key: pipeline, "envId": env_id})


# ---------- 维护 ----------

class TestMaintenance:
    def test_start_maintenance_on_idle_env(self, session):
        result = maintenance_service.start(session, ADMIN, {"envId": "ENV-ARM-01",
                                                            "reason": "例行维护"})
        assert result["envId"] == "ENV-ARM-01"
        record = session.get(MaintenanceRecord, result["id"])
        assert record.status == "maintenance"
        assert record.created_by == ADMIN
        assert sorted(record.device_ids) == ["DEV-001", "DEV-002", "DEV-003", "DEV-004"]
        for device_id in record.device_ids:
            assert session.get(Device, device_id).operational_state == "maintenance"
        assert env_status(session, "ENV-ARM-01") == "maintenance"

    def test_start_maintenance_reason_validation(self, session):
        for reason in ("", "   ", "长" * 201, None, 42):
            expect_business_error(maintenance_service.start, session, ADMIN,
                                  {"envId": "ENV-ARM-01", "reason": reason},
                                  match="维护原因")

    def test_start_maintenance_only_for_idle(self, session):
        for env_id in ("ENV-X86-02", "ENV-ARM-05", "ENV-009", "ENV-ST-04"):
            expect_business_error(maintenance_service.start, session, ADMIN,
                                  {"envId": env_id, "reason": "不该成功"},
                                  match="只能对空闲环境开始维护")

    def test_start_maintenance_unknown_or_retired_env(self, session):
        expect_business_error(maintenance_service.start, session, ADMIN,
                              {"envId": "NOPE", "reason": "x"}, match="环境不存在或已移出资源池")

    def test_verify_maintenance_healthy_restores_devices(self, session):
        started = maintenance_service.start(session, ADMIN, {"envId": "ENV-ARM-01",
                                                             "reason": "例行维护"})
        result = maintenance_service.verify(session, MEMBER, started["id"], healthy=True)
        assert result == {"id": started["id"], "status": "success"}
        for device_id in ("DEV-001", "DEV-002", "DEV-003", "DEV-004"):
            device = session.get(Device, device_id)
            assert device.operational_state == "idle"
            assert device.health_state == "healthy"
            assert device.health_checked_at is not None
        record = session.get(MaintenanceRecord, started["id"])
        assert record.status == "success"
        assert record.result == "healthy"
        assert record.verified_by == MEMBER
        assert env_status(session, "ENV-ARM-01") == "available"

    def test_verify_maintenance_unhealthy_keeps_isolated(self, session):
        started = maintenance_service.start(session, ADMIN, {"envId": "ENV-ARM-01",
                                                             "reason": "异常排查"})
        result = maintenance_service.verify(session, ADMIN, started["id"], healthy=False)
        assert result["status"] == "isolated"
        for device_id in ("DEV-001", "DEV-002", "DEV-003", "DEV-004"):
            device = session.get(Device, device_id)
            assert device.operational_state == "isolated"
        record = session.get(MaintenanceRecord, started["id"])
        assert record.status == "isolated"
        assert record.result == "unhealthy"
        assert env_status(session, "ENV-ARM-01") == "isolated"

    def test_verify_finished_record_rejected(self, session):
        maintenance_service.verify(session, ADMIN, "MT-003", healthy=True)
        expect_business_error(maintenance_service.verify, session, ADMIN, "MT-003", True,
                              match="维护记录已结束")

    def test_verify_blocked_while_shared_device_externally_occupied(self, session):
        """共享设备仍被其他租约占用时不能复核释放（docs/02）。

        正常业务流不会走到该状态（维护中的设备借不出去），这里直接注入占用行验证守卫。
        """
        from app.services import WORKSPACE_ID, now
        # 先解除 seed 的 MT-003（ENV-ST-04 维护会经共享 DEV-014 卡住 ENV-NET-06）
        maintenance_service.verify(session, ADMIN, "MT-003", healthy=True)
        maintenance_service.start(session, ADMIN, {"envId": "ENV-NET-06", "reason": "网络维护"})
        session.add(Lease(id="LEASE-X", workspace_id=WORKSPACE_ID, kind="manual",
                          holder_subject=MEMBER, purpose="异常占用", status="active",
                          start_at=now(), expires_at=now()))
        session.flush()
        session.add(LeaseResource(workspace_id=WORKSPACE_ID, lease_id="LEASE-X",
                                  device_id="DEV-021", active=True))
        session.flush()
        record_id = session.execute(select(MaintenanceRecord).where(
            MaintenanceRecord.reason == "网络维护")).scalar_one().id
        expect_business_error(maintenance_service.verify, session, ADMIN, record_id, True,
                              match="仍有共享设备占用，无法完成健康复核")

    def test_seed_maintenance_verify_frees_shared_device(self, session):
        """MT-003 复核通过：ENV-ST-04 与共享方 ENV-NET-06 一同恢复可借。"""
        maintenance_service.verify(session, ADMIN, "MT-003", healthy=True)
        assert env_status(session, "ENV-ST-04") == "available"
        assert env_status(session, "ENV-NET-06") == "available"
        assert session.get(Device, "DEV-014").operational_state == "idle"


# ---------- 执行状态机 ----------

class TestRunStateMachine:
    def test_start_run_requires_published_config(self, session):
        expect_business_error(start, session, pipeline=DRAFT_CONFIG,
                              match="只有已发布配置可以启动任务")
        expect_business_error(start, session, pipeline="no-such@v1",
                              match="只有已发布配置可以启动任务")

    def test_start_run_by_definition_id_resolves_latest_published(self, session):
        """按 definition id 启动：解析最新已发布版本；draft 定义被拒。"""
        result = start(session, env_id="ENV-VM-03", pipeline="os-smoke-test")
        assert result["config"] == "os-smoke-test@v3"
        expect_business_error(start, session, env_id="ENV-ARM-01", pipeline="storage-chaos",
                              match="只有已发布配置可以启动任务")

    def test_start_run_architecture_mismatch_rejected(self, session):
        expect_business_error(start, session, env_id="ENV-VM-03", pipeline=ARM_CONFIG,
                              match="环境架构与流水线要求不匹配")

    def test_start_run_on_unavailable_env_rejected(self, session):
        expect_business_error(start, session, env_id="ENV-X86-02",
                              match="环境或共享设备正在执行任务")
        expect_business_error(start, session, env_id="ENV-008",  # x86_64，被 LEASE-1002 借用
                              match="环境或共享设备已被借用")
        expect_business_error(start, session, env_id="ENV-009",  # x86_64，MT-001 隔离
                              match="关联设备已隔离，等待健康复核")

    def test_start_run_happy_path(self, session):
        result = start(session, actor=MEMBER, env_id="ENV-VM-03")
        assert result["id"] == "RUN-2001"
        assert result["status"] == "running"
        assert result["config"] == "os-smoke-test@v3"
        assert result["user"] == "李四"
        assert result["cleanupState"] == "pending"
        run = session.get(PipelineRun, result["id"])
        assert run.lease_id is not None
        lease = session.get(Lease, run.lease_id)
        assert lease.kind == "run"
        assert lease.holder_subject == MEMBER
        resources = session.execute(select(LeaseResource).where(
            LeaseResource.lease_id == lease.id, LeaseResource.active.is_(True))).scalars().all()
        assert len(resources) == 3  # ENV-VM-03 的 3 台设备
        assert env_status(session, "ENV-VM-03") == "running"

    def test_finish_run_moves_to_cleaning(self, session):
        result = start(session, env_id="ENV-VM-03")
        finished = run_service.finish_run(session, MEMBER, result["id"], success=True)
        assert finished["status"] == "cleaning"
        assert finished["result"] == "success"
        assert finished["progress"] == 100
        assert finished["evidence"] == 4
        assert env_status(session, "ENV-VM-03") == "cleaning"
        # 租约仍持有，设备未释放
        run = session.get(PipelineRun, result["id"])
        active = session.execute(select(LeaseResource).where(
            LeaseResource.lease_id == run.lease_id, LeaseResource.active.is_(True))).scalars().all()
        assert len(active) == 3

    def test_finish_reconciling_run_allowed(self, session):
        finished = run_service.finish_run(session, ADMIN, "RUN-1025", success=False)
        assert finished["status"] == "cleaning"
        assert finished["result"] == "failed"

    def test_finish_run_state_guard(self, session):
        expect_business_error(run_service.finish_run, session, ADMIN, "RUN-1026", True,
                              match="任务当前状态不可结束")  # 已在 cleaning
        expect_business_error(run_service.finish_run, session, ADMIN, "RUN-1027", True,
                              match="任务当前状态不可结束")  # 已 success
        expect_business_error(run_service.finish_run, session, ADMIN, "RUN-9999", True,
                              match="任务当前状态不可结束")

    def test_cleanup_requires_cleaning_state(self, session):
        expect_business_error(run_service.cleanup_run, session, ADMIN, "RUN-1028", True,
                              match="必须确认任务终态后再清理释放")

    def test_cleanup_healthy_releases_devices(self, session):
        result = start(session, env_id="ENV-VM-03")
        run_service.finish_run(session, MEMBER, result["id"], success=True)
        cleaned = run_service.cleanup_run(session, MEMBER, result["id"], healthy=True)
        assert cleaned["status"] == "success"
        assert cleaned["cleanupState"] == "done"
        assert cleaned["evidence"] == 5
        run = session.get(PipelineRun, result["id"])
        active = session.execute(select(LeaseResource).where(
            LeaseResource.lease_id == run.lease_id, LeaseResource.active.is_(True))).scalars().all()
        assert active == []
        for device in session.execute(select(Device).join(
                Device.environments).where(Device.id.in_(
                    ["DEV-011", "DEV-012", "DEV-013"]))).scalars().all():
            assert device.health_state == "healthy"
        assert env_status(session, "ENV-VM-03") == "available"

    def test_cleanup_unhealthy_creates_isolation_record(self, session):
        result = start(session, env_id="ENV-VM-03")
        run_service.finish_run(session, MEMBER, result["id"], success=False)
        cleaned = run_service.cleanup_run(session, MEMBER, result["id"], healthy=False)
        assert cleaned["status"] == "failed"
        assert cleaned["cleanupState"] == "isolated"
        record = session.execute(select(MaintenanceRecord).where(
            MaintenanceRecord.environment_ids.contains(["ENV-VM-03"]))).scalar_one()
        assert record.status == "isolated"
        assert record.reason.startswith(result["id"])
        assert env_status(session, "ENV-VM-03") == "isolated"  # 清理异常 → 设备隔离

    def test_cleanup_unhealthy_marks_devices_isolated(self, session):
        result = start(session, env_id="ENV-VM-03")
        run_service.finish_run(session, MEMBER, result["id"], success=False)
        run_service.cleanup_run(session, MEMBER, result["id"], healthy=False)
        for device_id in ("DEV-011", "DEV-012", "DEV-013"):
            device = session.get(Device, device_id)
            assert device.operational_state == "isolated"
            assert device.health_state == "unhealthy"
        assert env_status(session, "ENV-VM-03") == "isolated"

    def test_seed_cleaning_run_cleanup_releases_lease(self, session):
        """seed 的 RUN-1026（cleaning）清理后应释放 ENV-013 设备（修复后验收基准）。"""
        cleaned = run_service.cleanup_run(session, ADMIN, "RUN-1026", healthy=True)
        assert cleaned["cleanupState"] == "done"
        assert env_status(session, "ENV-013") == "available"
        lease = session.get(Lease, "LEASE-R-RUN-1026")
        active = session.execute(select(LeaseResource).where(
            LeaseResource.lease_id == lease.id, LeaseResource.active.is_(True))).scalars().all()
        assert active == []

    def test_run_lease_expiry_eight_hours(self, session):
        from datetime import datetime, timezone
        result = start(session, env_id="ENV-VM-03")
        run = session.get(PipelineRun, result["id"])
        lease = session.get(Lease, run.lease_id)
        delta = lease.expires_at - lease.start_at
        assert delta.total_seconds() == 8 * 3600
        assert lease.expires_at > datetime.now(timezone.utc)
