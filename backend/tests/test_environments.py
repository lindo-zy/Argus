"""环境域测试：建档校验、编辑/占用保护、retire/restore、adjust_devices、健康检查、复制。

规则来源 docs/02「自助资源池」、docs/03 资源字段与生命周期，
并与 frontend/tests/resources.test.js 对齐。
"""
from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.models import (
    Device, Environment, EnvironmentDevice, LeaseEnvironment, MaintenanceRecord,
)
from app.services import devices as device_service
from app.services import environments as env_service
from app.services import leases as lease_service
from conftest import expect_business_error, expect_permission_error, revision_of

ADMIN = "U-1001"
MEMBER = "U-1003"

ENV_INPUT = {
    "name": "用户联合环境", "arch": "ARM64", "type": "physical",
    "description": "用户自己维护", "version": "OS 24.03 RC2",
    "location": "实验室 B", "deviceIds": ["DEV-001"],
}


def create_env(session, actor=ADMIN, **overrides):
    payload = {**ENV_INPUT, **overrides}
    return env_service.save_environment(session, actor, payload)


def make_device(session, device_id=None, arch="ARM64", type_="physical", name="测试设备"):
    """直接插入设备行（绕过 save_device 的建档缺陷，见 test_devices.py）。"""
    from app.services import WORKSPACE_ID
    device = Device(
        id=device_id or f"DEV-T{len(session.execute(select(Device.id)).scalars().all())+1:03d}",
        workspace_id=WORKSPACE_ID, name=name, architecture=arch, resource_type=type_,
        lifecycle="active", operational_state="idle", health_state="unknown",
        created_by=ADMIN)
    session.add(device)
    session.flush()
    return device


# ---------- 建档与编辑 ----------

class TestSaveEnvironment:
    def test_create_env_defaults_owner_and_tags(self, session):
        result = create_env(session)
        env = session.get(Environment, result["id"])
        assert env is not None
        assert result["id"] == "ENV-2001"  # 编号规则：max(2000, 现有最大后缀)+1
        assert env.owner_subject == "王磊"  # 新建档默认当前操作者
        # IP 取关联设备地址（environment_dict 派生字段）
        assert env_service.environment_dict(session, env, {})["ip"] == session.get(Device, "DEV-001").address
        assert env.tags == ["ARM64", "物理机"]
        assert env.lifecycle == "active"
        # 注：save_environment 对新建环境也调用 _topology_set，首次建档即递增，
        # 因此实际建档后 topology_version/row_version 均为 2（列默认值为 1）。
        assert env.topology_version == 2
        assert env.row_version == 2
        linked = session.execute(select(EnvironmentDevice.device_id).where(
            EnvironmentDevice.environment_id == env.id)).scalars().all()
        assert linked == ["DEV-001"]

    def test_create_env_owner_and_custom_tags(self, session):
        create_env(session, owner="李四", tags=["回归", "  "])
        env = session.execute(select(Environment).where(
            Environment.name == "用户联合环境")).scalar_one()
        assert env.owner_subject == "李四"
        assert env.tags == ["ARM64", "物理机", "回归"]  # 固定标签 + 去空白自定义

    def test_create_validation_errors(self, session):
        before = revision_of(session)
        cases = [
            ({"name": ""}, "环境名称"),
            ({"name": "长" * 61}, "环境名称"),
            ({"deviceIds": []}, "请至少关联一台有效设备"),
            ({"deviceIds": ["MISSING"]}, "不存在或已移出资源池"),
            ({"arch": "RISC-V"}, "处理器架构"),
            ({"type": "container"}, "物理机或虚拟机"),
            ({"description": "长" * 201}, "环境用途"),
            ({"version": "长" * 81}, "系统版本"),
            ({"location": "长" * 101}, "部署位置"),
            ({"tags": "not-a-list"}, "标签格式无效"),
            ({"tags": [1, 2]}, "标签格式无效"),
        ]
        for overrides, message in cases:
            expect_business_error(create_env, session, **overrides, match=message)
        # 架构/类型不一致的设备
        expect_business_error(create_env, session, deviceIds=["DEV-005"],
                              match="架构、类型必须与环境要求一致")
        assert revision_of(session) == before  # 全部拒绝：无任何事件
        assert session.execute(select(func.count()).select_from(Environment)).scalar_one() == 24

    def test_create_env_with_occupied_device_rejected(self, session):
        """DEV-018 属 ENV-ARM-05（LEASE-1001 借用中，ARM64 物理机），建档应被拒。"""
        expect_business_error(create_env, session, deviceIds=["DEV-018"],
                              match="正在使用")

    def test_create_env_requires_admin(self, session):
        expect_permission_error(env_service.save_environment, session, MEMBER,
                                dict(ENV_INPUT), match="新增环境")

    def test_edit_env_allowed_for_member(self, session):
        created = create_env(session)
        result = env_service.save_environment(session, MEMBER, {"id": created["id"], **ENV_INPUT})
        assert result["id"] == created["id"]

    def test_edit_metadata_updates_and_owner_kept(self, session):
        created = create_env(session, owner="李四")
        result = env_service.save_environment(session, ADMIN, {
            "id": created["id"], "name": "更新后的环境", "arch": "ARM64", "type": "physical",
            "description": "新描述", "version": "OS 24.10", "location": "实验室 C",
            "deviceIds": ["DEV-001"],
        })
        env = session.get(Environment, result["id"])
        assert env.name == "更新后的环境"
        assert env.owner_subject == "李四"  # 未传 owner 时沿用原负责人
        assert env.row_version == 3  # 建档 2 + 编辑 1
        assert env.os_version_text == "OS 24.10"

    def test_edit_topology_change_recorded(self, session):
        created = create_env(session)
        device = make_device(session, "DEV-T01")
        env_service.save_environment(session, ADMIN, {
            "id": created["id"], **ENV_INPUT, "deviceIds": ["DEV-001", device.id]})
        env = session.get(Environment, created["id"])
        assert env.topology_version == 3
        linked = session.execute(select(EnvironmentDevice.device_id).where(
            EnvironmentDevice.environment_id == created["id"])).scalars().all()
        assert sorted(linked) == ["DEV-001", device.id]

    def test_edit_occupied_env_rejected(self, session):
        """ENV-ARM-05 被手工借用占用（LEASE-1001）→ 编辑被拒。"""
        payload = {"id": "ENV-ARM-05", "name": "改名", "arch": "ARM64", "type": "physical",
                   "deviceIds": ["DEV-018"]}
        expect_business_error(env_service.save_environment, session, ADMIN, payload,
                              match="释放后才能调整资源配置")

    def test_edit_topology_to_occupied_device_rejected(self, session):
        payload = {"id": "ENV-ARM-01", "name": "ARM64 兼容性环境", "arch": "ARM64",
                   "type": "physical", "deviceIds": ["DEV-018"]}  # DEV-018 被 LEASE-1001 借用
        expect_business_error(env_service.save_environment, session, ADMIN, payload,
                              match="关联设备正在使用或维护")

    def test_edit_retired_env_rejected(self, session):
        env_service.archive_environment(session, ADMIN, "ENV-ARM-01")
        payload = {"id": "ENV-ARM-01", "name": "x", "arch": "ARM64", "type": "physical",
                   "deviceIds": ["DEV-001"]}
        expect_business_error(env_service.save_environment, session, ADMIN, payload,
                              match="环境不存在或已移出资源池")


# ---------- adjust_devices ----------

class TestAdjustDevices:
    def test_adjust_add_and_remove_updates_topology_version(self, session):
        device = make_device(session, "DEV-T01")
        result = env_service.adjust_devices(
            session, MEMBER, "ENV-ARM-01", add=[device.id], remove=["DEV-001"])
        assert sorted(result["deviceIds"]) == ["DEV-002", "DEV-003", "DEV-004", device.id]
        env = session.get(Environment, "ENV-ARM-01")
        assert env.topology_version == 2
        assert env.row_version == 2

    def test_adjust_rejects_remove_all(self, session):
        expect_business_error(env_service.adjust_devices, session, ADMIN, "ENV-ARM-01",
                              add=[], remove=["DEV-001", "DEV-002", "DEV-003", "DEV-004"],
                              match="环境至少需要关联一台设备，不能全部摘除")

    def test_adjust_rejects_unknown_remove(self, session):
        expect_business_error(env_service.adjust_devices, session, ADMIN, "ENV-ARM-01",
                              add=[], remove=["DEV-999"],
                              match="设备 DEV-999 不在该环境的关联列表中")

    def test_adjust_rejects_occupied_env(self, session):
        expect_business_error(env_service.adjust_devices, session, ADMIN, "ENV-ARM-05",
                              add=[], remove=["DEV-018"],
                              match="释放后才能调整资源配置")

    def test_adjust_rejects_adding_busy_device(self, session):
        expect_business_error(env_service.adjust_devices, session, ADMIN, "ENV-ARM-01",
                              add=["DEV-018"], remove=[],  # DEV-018 被 LEASE-1001 占用
                              match="关联设备正在使用或维护")

    def test_adjust_rejects_arch_mismatch(self, session):
        expect_business_error(env_service.adjust_devices, session, ADMIN, "ENV-ARM-01",
                              add=["DEV-005"], remove=[],
                              match="架构、类型必须与环境要求一致")

    def test_adjust_rejects_retired_env_and_unknown_env(self, session):
        env_service.archive_environment(session, ADMIN, "ENV-ARM-01")
        expect_business_error(env_service.adjust_devices, session, ADMIN, "ENV-ARM-01",
                              add=[], remove=["DEV-001"], match="环境不存在或已移出资源池")
        expect_business_error(env_service.adjust_devices, session, ADMIN, "NOPE",
                              add=[], remove=[], match="环境不存在或已移出资源池")

    def test_adjust_after_borrow_returns_checking_rejected(self, session):
        result = lease_service.borrow(session, ADMIN,
                                      {"envIds": ["ENV-ARM-01"], "hours": 4,
                                       "purpose": "占用后调整"})
        lease_service.request_return(session, ADMIN, result["lease"]["id"])
        expect_business_error(env_service.adjust_devices, session, ADMIN, "ENV-ARM-01",
                              add=[], remove=["DEV-001"],
                              match="释放后才能调整资源配置")


# ---------- retire / restore ----------

class TestRetireRestore:
    def test_retire_requires_admin(self, session):
        expect_permission_error(env_service.archive_environment, session, MEMBER,
                                "ENV-ARM-01", match="移出环境仅管理员可执行")

    def test_retire_marks_lifecycle_and_hides_from_default_list(self, session):
        result = env_service.archive_environment(session, ADMIN, "ENV-ARM-01")
        assert result == {"id": "ENV-ARM-01", "name": "ARM64 兼容性环境"}
        env = session.get(Environment, "ENV-ARM-01")
        assert env.lifecycle == "retired"
        # 默认列表消失
        from app.api import list_environments
        visible = list_environments(session)["environments"]
        assert all(e["id"] != "ENV-ARM-01" for e in visible)
        with_retired = list_environments(session, include_retired=True)["environments"]
        assert any(e["id"] == "ENV-ARM-01" and e["lifecycle"] == "retired"
                   for e in with_retired)

    def test_retired_env_borrow_skipped_with_code(self, session):
        env_service.archive_environment(session, ADMIN, "ENV-ARM-01")
        result = lease_service.borrow(session, ADMIN, {
            "envIds": ["ENV-ARM-01"], "hours": 4, "purpose": "借退役环境"})
        assert result["lease"] is None
        assert result["outcomes"][0] == {
            "envId": "ENV-ARM-01", "name": "ARM64 兼容性环境",
            "status": "skipped", "code": "retired", "reason": "环境已移出资源池"}

    def test_retire_occupied_env_rejected(self, session):
        expect_business_error(env_service.archive_environment, session, ADMIN, "ENV-ARM-05",
                              match="释放后才能调整资源配置")

    def test_restore_success_returns_to_pool(self, session):
        env_service.archive_environment(session, ADMIN, "ENV-ARM-01")
        result = env_service.restore_environment(session, ADMIN, "ENV-ARM-01")
        assert result == {"id": "ENV-ARM-01", "name": "ARM64 兼容性环境"}
        env = session.get(Environment, "ENV-ARM-01")
        assert env.lifecycle == "active"
        assert env.topology_version == 2  # restore 递增拓扑版本
        from app.api import list_environments
        assert any(e["id"] == "ENV-ARM-01" for e in list_environments(session)["environments"])

    def test_restore_requires_admin(self, session):
        env_service.archive_environment(session, ADMIN, "ENV-ARM-01")
        expect_permission_error(env_service.restore_environment, session, MEMBER,
                                "ENV-ARM-01", match="恢复入池仅管理员可执行")

    def test_restore_non_retired_or_missing_rejected(self, session):
        expect_business_error(env_service.restore_environment, session, ADMIN, "ENV-VM-03",
                              match="该环境不在已移出状态，无需恢复")
        expect_business_error(env_service.restore_environment, session, ADMIN, "NOPE",
                              match="环境不存在")

    def test_restore_with_retired_device_reports_message(self, session):
        """设备失效场景：关联设备已移出 → 报「已移出或不存在，请先调整关联」。"""
        env_service.archive_environment(session, ADMIN, "ENV-VM-03")
        # 环境移出后设备不再被 active 环境引用，可移出
        device_service.retire_device(session, ADMIN, "DEV-011")
        expect_business_error(env_service.restore_environment, session, ADMIN, "ENV-VM-03",
                              match="关联设备 DEV-011 已移出或不存在，请先调整关联")

    def test_restore_with_occupied_device_reports_message(self, session):
        env_service.archive_environment(session, ADMIN, "ENV-ARM-01")
        # DEV-001 同时被新建环境借用 → 占用中
        create_env(session, name="借用 DEV-001 的环境")
        lease_service.borrow(session, MEMBER, {
            "envIds": ["ENV-2001"], "hours": 4, "purpose": "占用共享设备"})
        expect_business_error(env_service.restore_environment, session, ADMIN, "ENV-ARM-01",
                              match="正被占用或维护")

    def test_restore_without_devices_reports_message(self, session):
        from app.models import Base
        env_service.archive_environment(session, ADMIN, "ENV-ARM-01")
        # 构造无关联设备边界（正常接口不允许全部摘除，这里直接清理关联行）
        session.execute(EnvironmentDevice.__table__.delete().where(
            EnvironmentDevice.environment_id == "ENV-ARM-01"))
        session.flush()
        expect_business_error(env_service.restore_environment, session, ADMIN, "ENV-ARM-01",
                              match="环境没有关联设备，请先编辑关联后再恢复")


# ---------- 健康检查 ----------

class TestHealthCheck:
    def test_health_check_healthy_updates_devices(self, session):
        result = env_service.health_check(session, MEMBER, "ENV-ARM-01", healthy=True,
                                          note="例行巡检")
        assert result["healthy"] is True
        for device_id in ("DEV-001", "DEV-002", "DEV-003", "DEV-004"):
            device = session.get(Device, device_id)
            assert device.health_state == "healthy"
            assert device.health_checked_at is not None
        assert env_service.environment_dict(session, session.get(Environment, "ENV-ARM-01"),
                                            {})["health"] != "待复核"

    def test_health_check_unhealthy_isolates_and_creates_record(self, session):
        result = env_service.health_check(session, MEMBER, "ENV-ARM-01", healthy=False,
                                          note="磁盘告警")
        assert result["healthy"] is False
        for device_id in ("DEV-001", "DEV-002", "DEV-003", "DEV-004"):
            device = session.get(Device, device_id)
            assert device.operational_state == "isolated"
            assert device.health_state == "unhealthy"
        record = session.execute(select(MaintenanceRecord).where(
            MaintenanceRecord.environment_ids.contains(["ENV-ARM-01"]))).scalar_one()
        assert record.status == "isolated"
        assert record.result == "unhealthy"
        assert sorted(record.device_ids) == ["DEV-001", "DEV-002", "DEV-003", "DEV-004"]

    def test_health_check_busy_env_rejected(self, session):
        expect_business_error(env_service.health_check, session, ADMIN, "ENV-ARM-05", True,
                              match="环境正在占用或维护中，不能发起健康检查")
        expect_business_error(env_service.health_check, session, ADMIN, "ENV-ST-04", True,
                              match="环境正在占用或维护中，不能发起健康检查")

    def test_health_check_note_too_long_rejected(self, session):
        expect_business_error(env_service.health_check, session, ADMIN, "ENV-ARM-01",
                              True, "长" * 201, match="检查说明")


# ---------- 复制 ----------

class TestCopyEnvironment:
    def test_copy_requires_admin(self, session):
        expect_permission_error(env_service.copy_environment, session, MEMBER, "ENV-ARM-01",
                                device_ids=["DEV-002"], match="新增环境")

    def test_copy_with_devices_creates_copy(self, session):
        result = env_service.copy_environment(session, ADMIN, "ENV-ARM-01",
                                              device_ids=["DEV-002"])
        env = session.get(Environment, result["id"])
        assert env.name == "ARM64 兼容性环境 副本"
        assert env.architecture == "ARM64"
        assert env.owner_subject == "张明"  # 沿用来源负责人（seed 中 ENV-ARM-01 负责人为张明）
        assert env.description == "兼容性验证"

    def test_copy_without_devices_rejected(self, session):
        expect_business_error(env_service.copy_environment, session, ADMIN, "ENV-ARM-01",
                              match="请至少关联一台有效设备")

    def test_copy_from_retired_source_allowed(self, session):
        env_service.archive_environment(session, ADMIN, "ENV-ARM-01")
        result = env_service.copy_environment(session, ADMIN, "ENV-ARM-01",
                                              device_ids=["DEV-002"])
        env = session.get(Environment, result["id"])
        assert env.lifecycle == "active"

    def test_copy_missing_source_rejected(self, session):
        expect_business_error(env_service.copy_environment, session, ADMIN, "NOPE",
                              device_ids=["DEV-001"], match="环境不存在或已移出资源池")
