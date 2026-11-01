"""服务层公共工具：校验、ID 生成、事件写入、身份与权限。

服务函数入口无关（REST 与 MCP 共用），actor 由调用方显式传入。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..errors import ARCHES, RESOURCE_TYPES, BusinessError, PermissionError
from ..models import ResourceEvent, User, Workspace

WORKSPACE_ID = settings().default_workspace_id


def now() -> datetime:
    return datetime.now(timezone.utc)


def text_field(value: Any, label: str, max_len: int, required: bool = False) -> str:
    """文本校验：类型、长度、去首尾空格（等价 mock 的 text()）。"""
    if value is not None and not isinstance(value, str):
        raise BusinessError(f"{label}格式无效。")
    result = (value or "").strip()
    if (required and not result) or len(result) > max_len:
        raise BusinessError(f"{label}{'不能为空，且' if required else ''}不能超过 {max_len} 字。")
    return result


def check_platform(arch: Any, type_: Any) -> None:
    if arch not in ARCHES:
        raise BusinessError("请选择有效的处理器架构。")
    if type_ not in RESOURCE_TYPES:
        raise BusinessError("请选择物理机或虚拟机类型。")


def next_id(session: Session, model, prefix: str) -> str:
    """与 mock 相同的编号规则：max(2000, 现有最大数字后缀) + 1。"""
    rows = session.execute(select(model.id)).scalars().all()
    max_suffix = max((int(rid.split("-")[-1]) if str(rid).split("-")[-1].isdigit() else 0 for rid in rows), default=0)
    return f"{prefix}-{max(2000, max_suffix) + 1}"


def get_user(session: Session, user_id: str) -> User:
    user = session.get(User, user_id or "")
    if not user:
        raise BusinessError("身份不存在或已失效，请刷新后重试。", code="unknown_actor")
    if user.status != "active":
        raise BusinessError("该身份已停用，不能执行操作。", code="actor_disabled")
    return user


def require_admin(session: Session, actor_id: str, what: str) -> User:
    user = get_user(session, actor_id)
    if user.role != "admin":
        raise PermissionError(f"{what}仅管理员可执行，当前身份「{user.name}」为普通人员。")
    return user


def record_event(session: Session, *, actor_id: str, action: str, entity_type: str,
                 entity_id: str, payload: dict | None = None,
                 result_snapshot: dict | None = None, tone: str = "blue",
                 request_key: str = "", request_hash: str = "") -> None:
    session.add(ResourceEvent(
        workspace_id=WORKSPACE_ID, actor_subject=actor_id, action=action,
        entity_type=entity_type, entity_id=entity_id, request_key=request_key,
        request_hash=request_hash, payload=payload or {},
        result_snapshot=result_snapshot or {}, tone=tone,
    ))


def ensure_workspace(session: Session) -> None:
    if not session.get(Workspace, WORKSPACE_ID):
        session.add(Workspace(id=WORKSPACE_ID, name="系统测试 · 演示工作空间"))
        session.flush()


def revision_of(session: Session) -> int:
    latest = session.execute(select(func.max(ResourceEvent.id))).scalar()
    return int(latest or 0)
