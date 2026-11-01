"""资源变更统一按环境 ID、设备 ID 排序加锁；锁内仅做数据库操作。"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Device, Environment, EnvironmentDevice
from . import WORKSPACE_ID


def lock_resources(session: Session, env_ids=(), device_ids=()) -> None:
    session.execute(select(Environment).where(
        Environment.workspace_id == WORKSPACE_ID, Environment.id.in_(list(env_ids))
    ).order_by(Environment.id).with_for_update().execution_options(populate_existing=True)).scalars().all()
    linked = session.execute(select(EnvironmentDevice.device_id).where(
        EnvironmentDevice.workspace_id == WORKSPACE_ID,
        EnvironmentDevice.environment_id.in_(list(env_ids)),
    )).scalars().all()
    ids = sorted(set(linked) | set(device_ids))
    session.execute(select(Device).where(
        Device.workspace_id == WORKSPACE_ID, Device.id.in_(ids)
    ).order_by(Device.id).with_for_update().execution_options(populate_existing=True)).scalars().all()
