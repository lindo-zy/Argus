"""领域异常与状态常量。"""
from __future__ import annotations


class BusinessError(Exception):
    """业务规则拒绝；message 面向最终用户（中文）。"""

    def __init__(self, message: str, *, code: str = "business_rule") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class PermissionError(BusinessError):
    """权限不足（管理员专属操作）。"""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="permission_denied")


# 设备 operational_state：维护/隔离是资产事实；占用由租约与执行派生，不落设备表。
OP_IDLE = "idle"
OP_MAINTENANCE = "maintenance"
OP_ISOLATED = "isolated"

HEALTH_HEALTHY = "healthy"
HEALTH_UNHEALTHY = "unhealthy"
HEALTH_UNKNOWN = "unknown"

# 租约状态机（manual / run / maintenance 共用）。
LEASE_ACTIVE = "active"
LEASE_CHECKING = "checking"
LEASE_RELEASED = "released"
LEASE_ISOLATED = "isolated"

# 演示执行状态机。
RUN_STATES = ["running", "reconciling", "cleaning", "success", "failed"]

ARCHES = ("ARM64", "x86_64")
RESOURCE_TYPES = ("physical", "virtual")

# 占用中的派生设备状态（由租约/执行计算，等价 mock 的 occupied 判定）。
OCCUPIED_STATES = ("leased", "running", "checking", "cleaning", "reconciling", "maintenance")
# 环境派生优先级：隔离 > 维护 > 对账 > 清理 > 复核 > 执行 > 借用。
STATUS_PRIORITY = ["isolated", "maintenance", "reconciling", "cleaning", "checking", "running", "leased"]
