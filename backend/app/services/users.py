"""人员与权限领域服务：新增、角色、停用/启用（仅管理员）。"""
from __future__ import annotations

import secrets

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..errors import BusinessError
from ..models import User
from . import WORKSPACE_ID, get_user, next_id, record_event, require_admin, text_field

ROLES = ("admin", "member")


def active_admins(session: Session) -> list[User]:
    return list(session.execute(
        select(User).where(User.workspace_id == WORKSPACE_ID, User.role == "admin",
                           User.status == "active")
    ).scalars().all())


def list_users(session: Session) -> list[User]:
    return list(session.execute(
        select(User).where(User.workspace_id == WORKSPACE_ID).order_by(User.id)
    ).scalars().all())


def add_user(session: Session, actor_id: str, payload: dict) -> dict:
    actor = require_admin(session, actor_id, "新增人员")
    name = text_field(payload.get("name"), "人员姓名", 20, required=True)
    role = payload.get("role", "member")
    if role not in ROLES:
        raise BusinessError("请选择有效的角色。")
    if session.execute(select(func.count()).select_from(User).where(User.name == name)).scalar_one():
        raise BusinessError("同名人员已存在。")
    user = User(id=next_id(session, User, "U"), workspace_id=WORKSPACE_ID, name=name,
                role=role, status="active",
                title="系统测试组 · 管理员" if role == "admin" else "系统测试组 · 成员",
                api_token=f"argus-{secrets.token_hex(12)}")
    session.add(user)
    record_event(session, actor_id=actor.id, action="user_add", entity_type="user",
                 entity_id=user.id, tone="green",
                 payload={"text": f"{actor.name} 新增了{'管理员' if role == 'admin' else '人员'} {name}"})
    session.flush()
    return user_dict(user)


def _target(session: Session, user_id: str) -> User:
    """目标读取不经过启用校验：被停用人员必须可以重新启用。"""
    user = session.get(User, user_id or "")
    if not user:
        raise BusinessError("人员不存在，请刷新后重试。")
    return user


def set_role(session: Session, actor_id: str, user_id: str, role: str) -> dict:
    actor = require_admin(session, actor_id, "调整角色")
    user = _target(session, user_id)
    if user.id == actor.id:
        raise BusinessError("不能调整当前身份自己的角色。")
    if role not in ROLES:
        raise BusinessError("请选择有效的角色。")
    if user.role == "admin" and role == "member" and len(active_admins(session)) == 1:
        raise BusinessError("至少保留一名启用的管理员。")
    user.role = role
    record_event(session, actor_id=actor.id, action="user_role", entity_type="user",
                 entity_id=user.id, tone="green" if role == "admin" else "blue",
                 payload={"text": f"{actor.name} 将 {user.name} 设为{'管理员' if role == 'admin' else '普通人员'}"})
    session.flush()
    return user_dict(user)


def set_status(session: Session, actor_id: str, user_id: str, status: str) -> dict:
    actor = require_admin(session, actor_id, "停用或启用人员")
    user = _target(session, user_id)
    if user.id == actor.id:
        raise BusinessError("不能停用或启用当前身份自己。")
    if status not in ("active", "disabled"):
        raise BusinessError("状态无效。")
    if user.status == status:
        raise BusinessError("该人员已停用。" if status == "disabled" else "该人员已启用。")
    if user.role == "admin" and status == "disabled" and len(active_admins(session)) == 1:
        raise BusinessError("至少保留一名启用的管理员。")
    user.status = status
    record_event(session, actor_id=actor.id, action="user_status", entity_type="user",
                 entity_id=user.id, tone="amber" if status == "disabled" else "green",
                 payload={"text": f"{actor.name} 已{'停用' if status == 'disabled' else '启用'} {user.name}"})
    session.flush()
    return user_dict(user)


def user_dict(user: User) -> dict:
    return {"id": user.id, "name": user.name, "role": user.role, "status": user.status,
            "title": user.title, "joined": user.created_at.date().isoformat()}


def resolve_by_token(session: Session, token: str) -> User | None:
    if not token:
        return None
    return session.execute(
        select(User).where(User.api_token == token, User.status == "active")
    ).scalar_one_or_none()
