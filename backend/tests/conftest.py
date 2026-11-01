"""测试环境：独立 argus_test 库 + 每用例 TRUNCATE + 重新 seed。

约定：
- 必须在导入任何 app 模块前设置 ARGUS_DATABASE_URL，使 app.db.engine /
  app.services.WORKSPACE_ID 等模块级初始化全部指向测试库。
- 每个用例前 TRUNCATE 全部业务表并重新注入演示样本（24 环境 / 58 设备 / 4 用户），
  保证用例彼此隔离；ENV-ARM-01 空闲可用、ENV-X86-02 执行中（RUN-1028）、
  ENV-ST-04 维护中（MT-003，与 ENV-NET-06 共享 DEV-014）、ENV-009 隔离（MT-001）。
"""
from __future__ import annotations

import os

TEST_DATABASE_URL = os.environ.get("ARGUS_TEST_DATABASE_URL", "postgresql+psycopg://argus:argus@127.0.0.1:5433/argus_test")
# The suite truncates all tables. Reject non-test database names even on overrides.
from sqlalchemy.engine import make_url
if not (make_url(TEST_DATABASE_URL).database or "").endswith("_test"):
    raise RuntimeError("ARGUS_TEST_DATABASE_URL 必须指向独立的 *_test 数据库。")
os.environ["ARGUS_DATABASE_URL"] = TEST_DATABASE_URL

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.errors import BusinessError, PermissionError
from app.models import Base, ResourceEvent
from app.seed import seed as seed_demo

_TRUNCATE = "TRUNCATE TABLE " + ", ".join(
    f'"{t}"' for t in Base.metadata.tables
) + " RESTART IDENTITY CASCADE"


@pytest.fixture(scope="session")
def engine():
    engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True, future=True)
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def lock_engine():
    """带 lock_timeout 的独立引擎：并发用例中限制 FOR UPDATE 等待上限，避免悬挂。"""
    engine = create_engine(
        TEST_DATABASE_URL, future=True,
        connect_args={"options": "-c lock_timeout=3000"})
    yield engine
    engine.dispose()


@pytest.fixture()
def session(engine):
    """每个用例：清库 -> seed -> 提供一个新 Session。"""
    with engine.begin() as conn:
        conn.execute(text(_TRUNCATE))
    seeder = Session(engine)
    try:
        seed_demo(seeder)
    finally:
        seeder.close()
    sess = Session(engine, expire_on_commit=False)
    try:
        yield sess
    finally:
        sess.close()


@pytest.fixture()
def fresh_session(engine):
    """额外独立会话：观察已提交数据 / 充当并发另一方。"""
    sess = Session(engine, expire_on_commit=False)
    try:
        yield sess
    finally:
        sess.close()


@pytest.fixture()
def client(session):
    """REST 测试客户端：dependency_overrides 覆盖 get_session 指向测试会话。

    不用上下文管理器（跳过 startup 事件），避免触碰 app.db.SessionLocal。
    """
    from app.db import get_session
    from app.main import app as fastapi_app

    fastapi_app.dependency_overrides[get_session] = lambda: session
    yield TestClient(fastapi_app)
    fastapi_app.dependency_overrides.pop(get_session, None)


# ---------- 断言辅助 ----------

def expect_business_error(func, *args, code: str | None = None, match: str | None = None,
                          **kwargs) -> BusinessError:
    """断言抛出 BusinessError（可选匹配 code / 消息），并原样返回异常供进一步断言。"""
    with pytest.raises(BusinessError) as excinfo:
        func(*args, **kwargs)
    err = excinfo.value
    if code is not None:
        assert err.code == code, f"期望错误码 {code!r}，实际 {err.code!r}（{err.message}）"
    if match is not None:
        assert match in err.message, f"期望消息包含 {match!r}，实际 {err.message!r}"
    return err


def expect_permission_error(func, *args, match: str | None = None, **kwargs) -> PermissionError:
    with pytest.raises(PermissionError) as excinfo:
        func(*args, **kwargs)
    err = excinfo.value
    assert err.code == "permission_denied"
    if match is not None:
        assert match in err.message, f"期望消息包含 {match!r}，实际 {err.message!r}"
    return err


def revision_of(session: Session) -> int:
    """当前 revision（resource_events 最大 id，即快照 revision 口径）。"""
    from sqlalchemy import func, select
    latest = session.execute(select(func.max(ResourceEvent.id))).scalar()
    return int(latest or 0)


def active_resource_count(session: Session, device_id: str | None = None) -> int:
    from sqlalchemy import func, select
    from app.models import LeaseResource
    stmt = select(func.count()).select_from(LeaseResource).where(LeaseResource.active.is_(True))
    if device_id:
        stmt = stmt.where(LeaseResource.device_id == device_id)
    return session.execute(stmt).scalar_one()


def env_status(session: Session, env_id: str) -> str:
    """从快照口径读取环境派生状态。"""
    from app.models import Environment
    from app.services.environments import environment_dict
    from app.state import device_occupancy
    env = session.get(Environment, env_id)
    assert env is not None, f"环境 {env_id} 不存在"
    return environment_dict(session, env, device_occupancy(session, "WS-DEFAULT"))["status"]
