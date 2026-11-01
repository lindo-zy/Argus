"""人员与权限域测试：admin-only、自我保护、最后一名管理员保护、同名/角色校验。

规则来源 docs/02/05 身份投影，对齐 frontend/tests/permissions.test.js。
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import User
from app.services import users as user_service
from conftest import expect_business_error, expect_permission_error

ADMIN = "U-1001"   # 王磊 admin
MEMBER = "U-1003"  # 李四 member


def admins(session):
    return user_service.active_admins(session)


class TestAdminOnly:
    def test_member_cannot_add_user(self, session):
        expect_permission_error(user_service.add_user, session, MEMBER,
                                {"name": "赵倩"}, match="新增人员仅管理员可执行")

    def test_member_cannot_set_role(self, session):
        expect_permission_error(user_service.set_role, session, MEMBER, "U-1002", "admin",
                                match="调整角色仅管理员可执行")

    def test_member_cannot_set_status(self, session):
        expect_permission_error(user_service.set_status, session, MEMBER, "U-1002", "disabled",
                                match="停用或启用人员仅管理员可执行")


class TestAddUser:
    def test_add_member_and_admin(self, session):
        added = user_service.add_user(session, ADMIN, {"name": "赵倩", "role": "member"})
        assert added["id"] == "U-2001"
        assert added["status"] == "active"
        assert added["role"] == "member"
        assert added["title"] == "系统测试组 · 成员"

        promoted = user_service.add_user(session, ADMIN, {"name": "孙八", "role": "admin"})
        assert promoted["role"] == "admin"
        assert len(admins(session)) == 2

    def test_add_user_default_role_member(self, session):
        added = user_service.add_user(session, ADMIN, {"name": "赵倩"})
        assert added["role"] == "member"

    def test_add_duplicate_name_rejected(self, session):
        user_service.add_user(session, ADMIN, {"name": "赵倩"})
        expect_business_error(user_service.add_user, session, ADMIN, {"name": "赵倩"},
                              match="同名人员已存在")
        # 与 seed 用户同名同样拒绝
        expect_business_error(user_service.add_user, session, ADMIN, {"name": "王磊"},
                              match="同名人员已存在")

    def test_add_user_validation(self, session):
        expect_business_error(user_service.add_user, session, ADMIN, {"name": ""},
                              match="人员姓名")
        expect_business_error(user_service.add_user, session, ADMIN, {"name": "长" * 21},
                              match="人员姓名")
        expect_business_error(user_service.add_user, session, ADMIN,
                              {"name": "赵倩", "role": "owner"}, match="请选择有效的角色")


class TestSelfGuard:
    def test_admin_cannot_change_own_role(self, session):
        expect_business_error(user_service.set_role, session, ADMIN, ADMIN, "member",
                              match="不能调整当前身份自己的角色")

    def test_admin_cannot_disable_self(self, session):
        expect_business_error(user_service.set_status, session, ADMIN, ADMIN, "disabled",
                              match="不能停用或启用当前身份自己")

    def test_role_validated_before_apply(self, session):
        expect_business_error(user_service.set_role, session, ADMIN, "U-1002", "owner",
                              match="请选择有效的角色")

    def test_status_noop_rejected(self, session):
        expect_business_error(user_service.set_status, session, ADMIN, "U-1002", "active",
                              match="该人员已启用")
        expect_business_error(user_service.set_status, session, ADMIN, "U-1002", "bogus",
                              match="状态无效")


class TestLastAdminProtection:
    def test_demote_second_admin_then_invariant_holds(self, session):
        """第二名管理员降级允许，但永远无法通过接口产生 0 名启用管理员。"""
        user_service.set_role(session, ADMIN, "U-1002", "admin")
        user_service.set_role(session, ADMIN, "U-1002", "member")  # 仍剩 U-1001
        assert len(admins(session)) == 1

    def test_last_admin_cannot_be_removed_or_demoted(self, session):
        """只剩一名启用管理员时：本人被自我保护拦下，也不存在其他管理员可执行操作。

        注：users.set_role/set_status 内的「至少保留一名启用的管理员」守卫实际不可达
        （目标为停用身份时 get_user 先抛 actor_disabled，唯一启用管理员只能是自己、
        受自我保护拦截），属于防御性代码；此处验证对外可观察的不变量。
        """
        # 唯一管理员 U-1001 不能降级/停用自己
        expect_business_error(user_service.set_role, session, ADMIN, ADMIN, "member",
                              match="不能调整当前身份自己的角色")
        expect_business_error(user_service.set_status, session, ADMIN, ADMIN, "disabled",
                              match="不能停用或启用当前身份自己")
        assert len(admins(session)) == 1

    def test_disabled_admin_cannot_act(self, session):
        """停用的管理员尝试操作 → 身份停用错误。"""
        user_service.set_role(session, ADMIN, "U-1002", "admin")
        user_service.set_status(session, ADMIN, "U-1002", "disabled")
        expect_business_error(user_service.set_status, session, "U-1002", "U-1003", "disabled",
                              code="actor_disabled", match="该身份已停用")

    def test_disabled_user_can_be_re_enabled(self, session):
        user_service.set_status(session, ADMIN, "U-1002", "disabled")
        result = user_service.set_status(session, ADMIN, "U-1002", "active")
        assert result["status"] == "active"

    def test_promotion_takes_effect_immediately(self, session):
        """对齐 permissions.test.js：升管理员后立即拥有删除类权限。"""
        user_service.set_role(session, ADMIN, "U-1002", "admin")
        from app.services import environments as env_service
        result = env_service.archive_environment(session, "U-1002", "ENV-ARM-01")
        assert result["id"] == "ENV-ARM-01"

    def test_disabled_user_cannot_resolve_by_token(self, session):
        user_service.set_status(session, ADMIN, "U-1002", "disabled")
        assert user_service.resolve_by_token(session, "argus-token-zhangming") is None
