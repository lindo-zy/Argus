"""借用域测试：批量部分成功、共享设备去重、并发互斥、幂等、校验、续期/归还/复核。

规则来源 docs/02「批量直接借用」「归还、维护与执行」、docs/03 result 契约、
docs/07 幂等与互斥约束，并与 frontend/tests/multi-borrow.test.js、permissions.test.js 对齐。
"""
from __future__ import annotations

import threading

import pytest
from sqlalchemy import func, select

from app.errors import BusinessError
from app.models import (
    Device, EnvironmentDevice, Lease, LeaseEnvironment, LeaseResource,
    MaintenanceRecord, ResourceEvent,
)
from app.services import leases as lease_service
from app.services import maintenance as maintenance_service
from conftest import (
    active_resource_count, expect_business_error, env_status, revision_of,
)

ADMIN = "U-1001"   # 王磊 admin
MEMBER = "U-1003"  # 李四 member


def borrow_payload(env_ids, hours=8, purpose="多环境联合验证"):
    return {"envIds": env_ids, "hours": hours, "purpose": purpose}


def do_borrow(session, actor=ADMIN, env_ids=("ENV-ARM-01",), request_key="", **kw):
    return lease_service.borrow(session, actor, borrow_payload(env_ids, **kw),
                                request_key=request_key)


def outcome_of(result, env_id):
    return next(o for o in result["outcomes"] if o["envId"] == env_id)


def lease_resource_ids(session, lease_id, active_only=True):
    stmt = select(LeaseResource.device_id).where(LeaseResource.lease_id == lease_id)
    if active_only:
        stmt = stmt.where(LeaseResource.active.is_(True))
    return sorted(session.execute(stmt).scalars().all())


# ---------- 1. 批量部分成功 / 全失败 ----------

class TestBatchBorrow:
    def test_partial_success_creates_single_lease_with_skipped_reasons(self, session):
        before_leases = session.execute(select(func.count()).select_from(Lease)).scalar_one()
        x86_dev = session.execute(select(EnvironmentDevice.device_id).where(
            EnvironmentDevice.environment_id == "ENV-X86-02")).scalars().first()
        x86_active_before = active_resource_count(session, x86_dev)
        result = do_borrow(session, env_ids=["ENV-ARM-01", "ENV-X86-02", "ENV-VM-03"])

        lease = result["lease"]
        assert lease is not None
        assert lease["envIds"] == ["ENV-ARM-01", "ENV-VM-03"]
        assert lease["user"] == "王磊"
        assert lease["status"] == "active"
        assert lease["kind"] == "manual"
        # 7 台设备 = ENV-ARM-01(4) + ENV-VM-03(3)，去重
        assert len(lease["deviceIds"]) == 7
        assert len(set(lease["deviceIds"])) == 7
        assert result["requestedEnvIds"] == ["ENV-ARM-01", "ENV-X86-02", "ENV-VM-03"]

        statuses = [o["status"] for o in result["outcomes"]]
        assert statuses == ["borrowed", "skipped", "borrowed"]
        running = outcome_of(result, "ENV-X86-02")
        assert running["code"] == "running"
        assert running["reason"] == "环境或共享设备正在执行任务"
        borrowed = outcome_of(result, "ENV-ARM-01")
        assert borrowed == {"envId": "ENV-ARM-01", "name": "ARM64 兼容性环境",
                            "status": "borrowed", "code": "available",
                            "reason": "空闲环境已直接借用，无需审批"}

        # 一张单 + 7 条 active 占用；ENV-X86-02 设备未被占用
        assert session.execute(select(func.count()).select_from(Lease)).scalar_one() \
            == before_leases + 1
        rows = session.execute(select(LeaseEnvironment).where(
            LeaseEnvironment.lease_id == lease["id"])).scalars().all()
        assert sorted(r.environment_id for r in rows) == ["ENV-ARM-01", "ENV-VM-03"]
        assert lease_resource_ids(session, lease["id"]) == sorted(lease["deviceIds"])
        # ENV-X86-02 的设备仅保留 seed 执行占用的那条 active 行，未被新租约占用
        assert active_resource_count(session, x86_dev) == x86_active_before == 1
        assert env_status(session, "ENV-X86-02") == "running"

    def test_skip_codes_for_each_unavailable_state(self, session):
        """维护/隔离/占用/ retired / 不存在各自给出对应 code。"""
        result = do_borrow(session, env_ids=[
            "ENV-ARM-01", "ENV-ST-04", "ENV-009", "ENV-ARM-05", "MISSING"])
        assert result["lease"]["envIds"] == ["ENV-ARM-01"]
        codes = {o["envId"]: o["code"] for o in result["outcomes"]}
        assert codes["ENV-ST-04"] == "maintenance"
        assert codes["ENV-009"] == "isolated"
        assert codes["ENV-ARM-05"] == "leased"
        assert codes["MISSING"] == "not_found"
        # ENV-ARM-05 与 ENV-009 被 lease/隔离事实占用，均未产生新占用
        assert outcome_of(result, "ENV-ARM-05")["reason"] == "环境或共享设备已被借用"

    def test_all_failed_returns_lease_none_without_new_occupancy(self, session):
        """全失败：不建单、不改占用，但按后端口径写入 batch_borrow_result 事件。"""
        before = revision_of(session)
        before_leases = session.execute(select(func.count()).select_from(Lease)).scalar_one()
        before_resources = session.execute(select(func.count()).select_from(LeaseResource)).scalar_one()

        result = do_borrow(session, env_ids=["ENV-X86-02", "ENV-ST-04", "MISSING"])

        assert result["lease"] is None
        assert all(o["status"] == "skipped" and o["reason"] for o in result["outcomes"])
        assert revision_of(session) == before + 1  # 仅事件推进
        event = session.get(ResourceEvent, before + 1)
        assert event.action == "batch_borrow_result"
        assert event.result_snapshot["lease"] is None
        assert session.execute(select(func.count()).select_from(Lease)).scalar_one() \
            == before_leases
        assert session.execute(select(func.count()).select_from(LeaseResource)).scalar_one() \
            == before_resources
        assert env_status(session, "ENV-ARM-01") == "available"

    def test_all_failed_event_does_not_break_retry_with_new_key(self, session):
        """全失败后换一批（新派生键）可正常借成功。"""
        do_borrow(session, env_ids=["ENV-X86-02"])
        result = do_borrow(session, env_ids=["ENV-ARM-01"])
        assert result["lease"]["envIds"] == ["ENV-ARM-01"]

    def test_legacy_single_env_id_accepted(self, session):
        payload = {"envId": "ENV-ARM-01", "hours": 4, "purpose": "旧字段兼容"}
        result = lease_service.borrow(session, MEMBER, payload)
        assert result["requestedEnvIds"] == ["ENV-ARM-01"]
        assert result["lease"]["envIds"] == ["ENV-ARM-01"]


# ---------- 2. 同批共享设备去重 ----------

class TestSharedDeviceDedup:
    def test_shared_device_claimed_once_across_batch(self, session):
        """ENV-ST-04 与 ENV-NET-06 共享 DEV-014：同批借用应一张单、设备去重。

        期望（与 mock 对齐）：先解除 MT-003 维护，borrow [ENV-ST-04, ENV-NET-06,
        ENV-ST-04] → 两环境均成功、共享设备 DEV-014 仅一条 active 占用、
        两个 device_ids_snapshot 都含 DEV-014。
        """
        maintenance_service.verify(session, ADMIN, "MT-003", healthy=True)
        result = do_borrow(session, env_ids=["ENV-ST-04", "ENV-NET-06", "ENV-ST-04"])

        lease = result["lease"]
        assert lease["envIds"] == ["ENV-ST-04", "ENV-NET-06"]  # 重复 ID 去重
        assert len(lease["deviceIds"]) == 6
        assert len(set(lease["deviceIds"])) == 6  # 4 + 3 - 1 共享

        snapshots = session.execute(select(LeaseEnvironment).where(
            LeaseEnvironment.lease_id == lease["id"])).scalars().all()
        by_env = {r.environment_id: r.device_ids_snapshot for r in snapshots}
        assert "DEV-014" in by_env["ENV-ST-04"]
        assert "DEV-014" in by_env["ENV-NET-06"]

        # 设备级权威占用：DEV-014 只一条 active 行，且归属同一租约
        assert lease_resource_ids(session, lease["id"]) == sorted(lease["deviceIds"])
        assert active_resource_count(session, "DEV-014") == 1
        row = session.execute(select(LeaseResource).where(
            LeaseResource.device_id == "DEV-014", LeaseResource.active.is_(True))).scalar_one()
        assert row.lease_id == lease["id"]

    def test_shared_device_batch_both_succeed_single_occupancy(self, session):
        """docs/02：同批 A、B 共用空闲设备 D，一起成功且 D 只占用一次。"""
        maintenance_service.verify(session, ADMIN, "MT-003", healthy=True)
        result = do_borrow(session, env_ids=["ENV-ST-04", "ENV-NET-06", "ENV-ST-04"])

        lease = result["lease"]
        assert lease is not None
        assert lease["envIds"] == ["ENV-ST-04", "ENV-NET-06"]
        assert outcome_of(result, "ENV-NET-06")["status"] == "borrowed"
        assert len(lease["deviceIds"]) == len(set(lease["deviceIds"]))  # 共享设备去重
        # 设备级权威占用：DEV-014 仅一条 active 行且归属该租约；两个环境快照都含它
        assert active_resource_count(session, "DEV-014") == 1
        row = session.execute(select(LeaseResource).where(
            LeaseResource.device_id == "DEV-014", LeaseResource.active.is_(True))).scalar_one()
        assert row.lease_id == lease["id"]
        snapshots = session.execute(select(LeaseEnvironment).where(
            LeaseEnvironment.lease_id == lease["id"])).scalars().all()
        assert len(snapshots) == 2
        for snapshot in snapshots:
            assert "DEV-014" in snapshot.device_ids_snapshot

    def test_shared_device_blocks_other_batch_after_borrowed(self, session):
        maintenance_service.verify(session, ADMIN, "MT-003", healthy=True)
        first = do_borrow(session, env_ids=["ENV-ST-04"], hours=4)
        second = do_borrow(session, actor=MEMBER, env_ids=["ENV-NET-06"], hours=4)
        # ENV-NET-06 的独有设备空闲，但共享设备 DEV-014 已被占用 → 整环境跳过
        assert second["lease"] is None
        assert outcome_of(second, "ENV-NET-06")["code"] == "leased"
        assert active_resource_count(session, "DEV-014") == 1
        assert first["lease"]["deviceIds"] is not None

    def test_return_flow_releases_shared_device_for_reuse(self, session):
        maintenance_service.verify(session, ADMIN, "MT-003", healthy=True)
        result = do_borrow(session, env_ids=["ENV-ST-04"], hours=4)
        lease_id = result["lease"]["id"]

        pending = lease_service.request_return(session, ADMIN, lease_id)
        assert pending["status"] == "checking"
        assert env_status(session, "ENV-ST-04") == "checking"
        # checking 期间其他批次不能借
        blocked = do_borrow(session, actor=MEMBER, env_ids=["ENV-NET-06"], hours=2)
        assert blocked["lease"] is None
        assert outcome_of(blocked, "ENV-NET-06")["code"] == "checking"

        released = lease_service.verify_return(session, ADMIN, lease_id, healthy=True)
        assert released["status"] == "released"
        assert lease_resource_ids(session, lease_id) == []  # 全部置 inactive
        assert env_status(session, "ENV-ST-04") == "available"
        assert env_status(session, "ENV-NET-06") == "available"
        # 注意换 hours/用途：与上面 blocked 尝试同派生键会幂等重放失败结果
        again = do_borrow(session, actor=MEMBER, env_ids=["ENV-NET-06"], hours=4)
        assert again["lease"] is not None


# ---------- 3. 并发互斥 ----------

class TestConcurrency:
    def test_sequential_borrow_after_commit_skips_occupied_env(self, session, fresh_session):
        """第一个借用提交后，第二个事务对同一环境只能跳过；设备仅一条 active 占用。"""
        first = do_borrow(session, env_ids=["ENV-ARM-01"], hours=4)
        session.commit()

        second = lease_service.borrow(fresh_session, MEMBER,
                                      borrow_payload(["ENV-ARM-01"], 4, "并发第二次借用"))
        assert second["lease"] is None
        assert outcome_of(second, "ENV-ARM-01")["code"] == "leased"
        assert outcome_of(second, "ENV-ARM-01")["status"] == "skipped"

        # 同一设备只有一条 active 占用（唯一索引兜底 + 业务互斥）
        for device_id in first["lease"]["deviceIds"]:
            assert active_resource_count(fresh_session, device_id) == 1

    def test_blocking_for_update_serializes_concurrent_borrows(self, session, lock_engine):
        """真实并发：A 事务持环境行锁未提交时 B 阻塞；A 提交后 B 读到占用并跳过。"""
        from sqlalchemy.orm import Session

        first = do_borrow(session, env_ids=["ENV-ARM-01"], hours=4)
        assert first["lease"] is not None
        # session 事务未提交，持有 ENV-ARM-01 行的 FOR UPDATE 锁

        second_session = Session(lock_engine, expire_on_commit=False)
        box: dict = {}

        def worker():
            try:
                box["result"] = lease_service.borrow(
                    second_session, MEMBER, borrow_payload(["ENV-ARM-01"], 4, "并发线程借用"))
            except BaseException as exc:  # pragma: no cover - 调试用
                box["error"] = exc

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(timeout=0.5)  # B 应阻塞在行锁上
        assert thread.is_alive(), "第二个借用应被 FOR UPDATE 阻塞而非立即返回"

        session.commit()  # A 提交释放锁
        thread.join(timeout=10)
        assert not thread.is_alive(), "A 提交后 B 应在 lock_timeout 内返回"
        assert "error" not in box, f"并发借用报错：{box.get('error')!r}"

        result = box["result"]
        assert result["lease"] is None
        assert outcome_of(result, "ENV-ARM-01")["code"] == "leased"
        for device_id in first["lease"]["deviceIds"]:
            assert active_resource_count(second_session, device_id) == 1
        second_session.rollback()
        second_session.close()


# ---------- 4. 幂等 ----------

class TestIdempotency:
    def test_same_request_key_returns_identical_result_without_new_occupancy(self, session):
        first = do_borrow(session, env_ids=["ENV-ARM-01", "ENV-VM-03"], request_key="retry-001")
        leases_before = session.execute(select(func.count()).select_from(Lease)).scalar_one()
        resources_before = session.execute(select(func.count()).select_from(LeaseResource)).scalar_one()

        second = do_borrow(session, env_ids=["ENV-ARM-01", "ENV-VM-03"], request_key="retry-001")

        assert second == first
        assert second["lease"]["id"] == first["lease"]["id"]
        assert session.execute(select(func.count()).select_from(Lease)).scalar_one() \
            == leases_before
        assert session.execute(select(func.count()).select_from(LeaseResource)).scalar_one() \
            == resources_before
        # 幂等重放不再新增事件
        events = session.execute(select(func.count()).select_from(ResourceEvent).where(
            ResourceEvent.request_key == "retry-001")).scalar_one()
        assert events == 1

    def test_derived_key_replay_without_explicit_key(self, session):
        """不传 request_key 时按 (actor, envIds, hours, purpose) 派生键幂等。"""
        payload = borrow_payload(["ENV-ARM-01"], 4, "派生键重放")
        first = lease_service.borrow(session, ADMIN, payload)
        second = lease_service.borrow(session, ADMIN, payload)
        assert second["lease"]["id"] == first["lease"]["id"]
        assert second == first

    def test_same_key_different_params_raises_request_conflict(self, session):
        do_borrow(session, env_ids=["ENV-ARM-01"], hours=4, request_key="retry-002")
        expect_business_error(
            do_borrow, session, env_ids=["ENV-VM-03"], hours=4, request_key="retry-002",
            code="request_conflict",
            match="幂等键已用于不同参数的借用请求",
        )

    def test_same_params_different_key_creates_second_lease_or_skips(self, session):
        """不同键=新请求：ENV-ARM-01 已被第一个键占用，第二个键整体跳过。"""
        do_borrow(session, env_ids=["ENV-ARM-01"], hours=4, request_key="key-a")
        result = do_borrow(session, env_ids=["ENV-ARM-01"], hours=4, request_key="key-b")
        assert result["lease"] is None
        assert outcome_of(result, "ENV-ARM-01")["code"] == "leased"

    def test_all_failure_result_is_idempotent_on_retry(self, session):
        first = do_borrow(session, env_ids=["ENV-X86-02"], request_key="fail-key")
        assert first["lease"] is None
        second = do_borrow(session, env_ids=["ENV-X86-02"], request_key="fail-key")
        assert second == first


# ---------- 5. 非法请求 ----------

class TestValidation:
    def test_invalid_hours_rejected_without_changes(self, session):
        for hours in (0, 1, 3, 5, 12, 96, None):
            before = revision_of(session)
            expect_business_error(do_borrow, session, env_ids=["ENV-ARM-01"], hours=hours,
                                  match="请选择有效的借用时长")
            assert revision_of(session) == before
        assert session.execute(select(func.count()).select_from(Lease)).scalar_one() == 9  # seed: 4 手工 + 5 执行

    def test_purpose_blank_or_too_long_rejected(self, session):
        for purpose in ("", "   ", "长" * 201, None, 42):
            expect_business_error(do_borrow, session, env_ids=["ENV-ARM-01"],
                                  purpose=purpose, match="请填写 1–200 字的借用用途")
        assert session.execute(select(func.count()).select_from(Lease)).scalar_one() == 9  # seed: 4 手工 + 5 执行

    def test_empty_or_invalid_env_ids_rejected(self, session):
        for env_ids in ([], None, [""], [None], [42], ["ENV-ARM-01", ""]):
            expect_business_error(do_borrow, session, env_ids=env_ids,
                                  match="请至少选择一个有效环境")
        assert session.execute(select(func.count()).select_from(Lease)).scalar_one() == 9  # seed: 4 手工 + 5 执行

    def test_purpose_stripped_and_boundary_200_ok(self, session):
        result = do_borrow(session, env_ids=["ENV-ARM-01"], purpose="  " + "联" * 200 + "  ")
        assert result["lease"]["purpose"] == "联" * 200

    def test_unknown_actor_rejected(self, session):
        expect_business_error(do_borrow, session, actor="U-9999",
                              code="unknown_actor", match="身份不存在")

    def test_disabled_actor_rejected(self, session):
        from app.services import users as user_service
        user_service.set_status(session, ADMIN, "U-1004", "disabled")
        session.commit()
        expect_business_error(do_borrow, session, actor="U-1004",
                              code="actor_disabled", match="该身份已停用")


# ---------- 6. 续期 / 归还 / 复核 ----------

class TestLeaseLifecycle:
    def test_renew_extends_own_active_lease_by_exact_hours(self, session):
        result = do_borrow(session, actor=MEMBER, env_ids=["ENV-VM-03"], hours=4)
        lease_id = result["lease"]["id"]
        old_end = result["lease"]["end"]

        renewed = lease_service.renew(session, MEMBER, lease_id, 4)
        assert renewed["id"] == lease_id
        from datetime import datetime
        delta = (datetime.fromisoformat(renewed["end"])
                 - datetime.fromisoformat(old_end)).total_seconds()
        assert delta == pytest.approx(4 * 3600, abs=2)

    def test_renew_seed_lease_expires_in_future(self, session):
        renewed = lease_service.renew(session, ADMIN, "LEASE-1001", 2)
        from datetime import datetime, timezone
        assert datetime.fromisoformat(renewed["end"]) > datetime.now(timezone.utc)

    def test_renew_rejects_invalid_hours_or_state(self, session):
        expect_business_error(lease_service.renew, session, ADMIN, "LEASE-1001", 3,
                              match="续期时长无效")
        borrowed = do_borrow(session, actor=MEMBER, env_ids=["ENV-VM-03"], hours=2)
        lease_service.request_return(session, MEMBER, borrowed["lease"]["id"])
        expect_business_error(lease_service.renew, session, MEMBER,
                              borrowed["lease"]["id"], 4, match="当前借用状态不可续期")

    def test_member_cannot_operate_admin_lease(self, session):
        """李四操作王磊的单 →「只能操作自己的借用记录」。"""
        for op in (lease_service.renew, lease_service.request_return):
            expect_business_error(op, session, MEMBER, "LEASE-1001",
                                  match="只能操作自己的借用记录")
        expect_business_error(lease_service.verify_return, session, MEMBER,
                              "LEASE-1001", True, match="只能操作自己的借用记录")

    def test_verify_return_requires_checking_state(self, session):
        borrowed = do_borrow(session, actor=MEMBER, env_ids=["ENV-VM-03"], hours=2)
        expect_business_error(lease_service.verify_return, session, MEMBER,
                              borrowed["lease"]["id"], True,
                              match="请先提交归还，完成后再进行健康复核")

    def test_verify_return_healthy_releases_all_devices(self, session):
        result = do_borrow(session, actor=MEMBER, env_ids=["ENV-ARM-01", "ENV-VM-03"], hours=4)
        lease_id = result["lease"]["id"]
        device_ids = result["lease"]["deviceIds"]

        lease_service.request_return(session, MEMBER, lease_id)
        released = lease_service.verify_return(session, MEMBER, lease_id, healthy=True)

        assert released["status"] == "released"
        assert lease_resource_ids(session, lease_id) == []
        for device_id in device_ids:
            device = session.get(Device, device_id)
            assert device.health_state == "healthy"
            assert device.health_checked_at is not None
        assert env_status(session, "ENV-ARM-01") == "available"
        assert env_status(session, "ENV-VM-03") == "available"
        # 释放后可再次借出
        again = do_borrow(session, env_ids=["ENV-ARM-01"], hours=2)
        assert again["lease"] is not None

    def test_verify_return_unhealthy_isolates_lease_and_creates_record(self, session):
        """复核异常：整单 isolated + 建维护记录；占用释放不再可借口径见隔离记录。

        注：设备是否应被标记 operational_state=isolated 属于已知偏差，
        见 test_verify_return_unhealthy_marks_devices_isolated。
        """
        result = do_borrow(session, actor=MEMBER, env_ids=["ENV-ARM-01"], hours=4)
        lease_id = result["lease"]["id"]
        lease_service.request_return(session, MEMBER, lease_id)

        isolated = lease_service.verify_return(session, MEMBER, lease_id, healthy=False)

        assert isolated["status"] == "isolated"
        record = session.execute(select(MaintenanceRecord).where(
            MaintenanceRecord.source_lease_id == lease_id)).scalar_one()
        assert record.status == "isolated"
        assert record.result == "unhealthy"
        assert record.environment_ids == ["ENV-ARM-01"]
        assert sorted(record.device_ids) == sorted(result["lease"]["deviceIds"])
        assert record.lease_id == lease_id
        assert lease_resource_ids(session, lease_id) == []

    def test_verify_return_unhealthy_marks_devices_isolated(self, session):
        """对照 mock verifyReturn(false)：整单设备置 isolated，可经维护复核恢复。"""
        result = do_borrow(session, actor=MEMBER, env_ids=["ENV-ARM-01"], hours=4)
        lease_id = result["lease"]["id"]
        lease_service.request_return(session, MEMBER, lease_id)
        lease_service.verify_return(session, MEMBER, lease_id, healthy=False)
        for device_id in result["lease"]["deviceIds"]:
            device = session.get(Device, device_id)
            assert device.operational_state == "isolated"
            assert device.health_state == "unhealthy"
        assert env_status(session, "ENV-ARM-01") == "isolated"

    def test_unhealthy_lease_recoverable_via_maintenance_verify(self, session):
        """隔离整单通过维护复核恢复可用（对照 multi-borrow.test.js 最后一例）。"""
        result = do_borrow(session, actor=MEMBER, env_ids=["ENV-ARM-01", "ENV-VM-03"], hours=4)
        lease_id = result["lease"]["id"]
        lease_service.request_return(session, MEMBER, lease_id)
        lease_service.verify_return(session, MEMBER, lease_id, healthy=False)
        record = session.execute(select(MaintenanceRecord).where(
            MaintenanceRecord.source_lease_id == lease_id)).scalar_one()

        maintenance_service.verify(session, MEMBER, record.id, healthy=True)

        assert env_status(session, "ENV-ARM-01") == "available"
        assert env_status(session, "ENV-VM-03") == "available"
