"""设备域测试：建档（已知崩溃缺陷）、编辑守卫、移出守卫、隔离状态不可经编辑解除。

规则来源 docs/02「自助资源池」、docs/03 资源字段，对齐 frontend/tests/resources.test.js。
"""
from __future__ import annotations

import pytest

from app.models import Device
from app.services import devices as device_service
from conftest import expect_business_error, expect_permission_error

ADMIN = "U-1001"
MEMBER = "U-1003"

DEVICE_INPUT = {
    "name": "用户新增设备", "arch": "ARM64", "type": "physical",
    "cpu": "32 核", "memory": "64 GB", "location": "实验室 B", "ip": "10.24.99.1",
}


def make_device(session, device_id="DEV-T01", arch="ARM64", type_="physical"):
    from app.services import WORKSPACE_ID
    device = Device(id=device_id, workspace_id=WORKSPACE_ID, name="测试设备",
                    architecture=arch, resource_type=type_, lifecycle="active",
                    operational_state="idle", health_state="unknown", created_by=ADMIN)
    session.add(device)
    session.flush()
    return device


class TestSaveDevice:
    def test_create_device(self, session):
        result = device_service.save_device(session, ADMIN, DEVICE_INPUT)
        assert result["id"] == "DEV-2001"  # 编号规则：max(2000, 现有最大后缀)+1
        device = session.get(Device, result["id"])
        assert device.lifecycle == "active"
        assert device.operational_state == "idle"
        assert device.health_state == "unknown"

    def test_create_device_requires_admin(self, session):
        expect_permission_error(device_service.save_device, session, MEMBER,
                                dict(DEVICE_INPUT), match="新增设备")

    def test_save_device_host(self, session):
        """虚拟机记录宿主机；物理机忽略宿主机输入。"""
        result = device_service.save_device(session, ADMIN, {**DEVICE_INPUT, "type": "virtual", "host": "host-09"})
        assert session.get(Device, result["id"]).host == "host-09"
        assert device_service.device_dict(session, session.get(Device, result["id"]), {})["host"] == "host-09"
        physical = device_service.save_device(session, ADMIN, {**DEVICE_INPUT, "host": "host-09"})
        assert session.get(Device, physical["id"]).host == ""

    def test_edit_device_updates_metadata(self, session):
        result = device_service.save_device(session, ADMIN, {"id": "DEV-001", **DEVICE_INPUT,
                                                             "name": "重新命名设备"})
        assert result["name"] == "重新命名设备"
        device = session.get(Device, "DEV-001")
        assert device.name == "重新命名设备"
        assert device.cpu == "32 核"
        assert device.address == "10.24.99.1"

    def test_edit_cannot_override_status_fields(self, session):
        """payload 携带状态覆写无效；隔离状态不随编辑解除（mock 行为：允许改元数据）。"""
        result = device_service.save_device(session, ADMIN, {
            "id": "DEV-027", "name": "保留隔离状态", "arch": "x86_64", "type": "physical",
            "status": "available"})
        device = session.get(Device, "DEV-027")
        assert device.name == "保留隔离状态"
        assert device.operational_state == "isolated"  # ENV-009 隔离设备

    def test_edit_busy_device_rejected(self, session):
        payload = {"id": "DEV-005", "name": "不能覆盖", "arch": "x86_64", "type": "physical",
                   "status": "available"}
        expect_business_error(device_service.save_device, session, ADMIN, payload,
                              match="设备正在使用、维护或等待复核，释放后才能编辑")

    def test_edit_arch_while_referenced_rejected(self, session):
        payload = {"id": "DEV-001", **DEVICE_INPUT, "arch": "x86_64"}
        expect_business_error(device_service.save_device, session, ADMIN, payload,
                              match="设备仍被环境引用，请先调整关联后再修改架构或类型")

    def test_validation_errors(self, session):
        expect_business_error(device_service.save_device, session, ADMIN,
                              {**DEVICE_INPUT, "name": ""}, match="设备名称")
        expect_business_error(device_service.save_device, session, ADMIN,
                              {**DEVICE_INPUT, "arch": "MIPS"}, match="处理器架构")
        expect_business_error(device_service.save_device, session, ADMIN,
                              {**DEVICE_INPUT, "ip": "长" * 101}, match="设备地址")


class TestRetireDevice:
    def test_retire_requires_admin(self, session):
        expect_permission_error(device_service.retire_device, session, MEMBER, "DEV-001",
                                match="移出设备仅管理员可执行")

    def test_retire_referenced_device_rejected(self, session):
        expect_business_error(device_service.retire_device, session, ADMIN, "DEV-001",
                              match="设备仍被环境引用，请先编辑关联环境或将环境移出资源池")

    def test_retire_busy_device_rejected(self, session):
        expect_business_error(device_service.retire_device, session, ADMIN, "DEV-005",
                              match="设备仍有占用或维护，释放后才能移出资源池")

    def test_retire_free_device_after_env_removed(self, session):
        from app.services import environments as env_service
        env_service.archive_environment(session, ADMIN, "ENV-ARM-01")
        device_service.retire_device(session, ADMIN, "DEV-001")
        device = session.get(Device, "DEV-001")
        assert device.lifecycle == "retired"

    def test_retired_device_cannot_be_edited(self, session):
        from app.services import environments as env_service
        env_service.archive_environment(session, ADMIN, "ENV-VM-03")
        device_service.retire_device(session, ADMIN, "DEV-011")
        expect_business_error(device_service.save_device, session, ADMIN,
                              {"id": "DEV-011", **DEVICE_INPUT, "arch": "x86_64",
                               "type": "physical"},
                              match="设备 DEV-011 不存在或已移出资源池")
