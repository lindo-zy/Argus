"""MCP 端到端冒烟测试：直连本地开发服务（uvicorn app.main:app --port 8000）的 streamable HTTP 入口。

默认目标 http://127.0.0.1:8000/mcp，可用环境变量 ARGUS_MCP_URL 覆盖（例如指向其他端口验证）。

注意：
- 直接读写 dev 数据库。借用用例借完立即归还复核释放；环境被并行任务占用或服务
  不可达时按约定 skip，而不是失败。
- .venv 未安装 pytest-asyncio，SDK 客户端又是 async：所有用例通过 asyncio.run
  的同步包装（_run / _call_*）执行，不引入额外依赖。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import uuid

import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from mcp.shared.exceptions import MCPError

MCP_URL = os.environ.get("ARGUS_MCP_URL", "http://127.0.0.1:8000/mcp")
ADMIN_TOKEN = "argus-demo-admin"   # U-1001 王磊（admin）
MEMBER_TOKEN = "argus-token-lisi"  # U-1003 李四（member）

EXPECTED_TOOLS = {
    "list_environments", "get_environment", "get_environment_timeline",
    "save_environment", "adjust_environment_devices", "retire_environment",
    "restore_environment", "check_environment_health", "list_devices",
    "create_device", "borrow_environments", "return_lease", "verify_return",
    "start_maintenance", "verify_maintenance", "get_snapshot_summary",
    "list_events", "start_demo_run",
}


def _run(coro):
    """同步包装：pytest-asyncio 不在场，用 asyncio.run 驱动 SDK 的 async 客户端。"""
    return asyncio.run(coro)


@contextlib.asynccontextmanager
async def mcp_session(token: str = ADMIN_TOKEN):
    """以 Bearer token 建立一条 MCP streamable HTTP 会话（initialize 完成）。"""
    http_client = create_mcp_http_client(
        headers={"Authorization": f"Bearer {token}"})
    async with streamable_http_client(MCP_URL, http_client=http_client) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


def _iter_exceptions(exc: BaseException):
    """anyio 任务组会把传输错误包进 ExceptionGroup，递归摊平便于判型。"""
    yield exc
    if isinstance(exc, BaseExceptionGroup):
        for sub in exc.exceptions:
            yield from _iter_exceptions(sub)


@pytest.fixture(scope="session")
def mcp_dev_server():
    """整套用例前的可达性探测：连接被拒按约定 skip；端点异常给出诊断信息。"""
    import httpx2

    async def _ping():
        async with mcp_session() as session:
            await session.list_tools()

    try:
        _run(_ping())
    except BaseException as exc:  # noqa: BLE001 —— 统一摊平后再分类
        transport_errors = [
            e for e in _iter_exceptions(exc)
            if isinstance(e, (httpx2.HTTPError, ConnectionError, OSError))
        ]
        if transport_errors:
            pytest.skip(f"开发服务不可达（{MCP_URL}），跳过 MCP 冒烟：{transport_errors[0]!r}")
        if any(isinstance(e, MCPError) for e in _iter_exceptions(exc)):
            pytest.fail(
                f"MCP 端点 {MCP_URL} 握手失败（{exc!r}）。已知线索：app.main 用 "
                "app.mount('/mcp', mcp_app()) 挂载，而 SDK 子应用内部注册的路径是 /mcp，"
                "实际端点为 /mcp/mcp；且子应用 lifespan 未随 FastAPI 启动，session manager "
                "未 run() 会让请求 500。可用 ARGUS_MCP_URL 覆盖目标端点重试。")
        raise


def _result_text(result) -> str:
    return "\n".join(getattr(block, "text", "") for block in result.content
                     if getattr(block, "text", None))


def _result_json(result):
    """工具结构化结果：优先 structuredContent，否则解析首个 text 里的 JSON。

    MCP 规范要求 structuredContent 是对象，SDK 会把 list 返回值包成
    {"result": [...]}，这里拆掉该信封还原原始返回。
    """
    data = result.structured_content
    if data is None:
        for block in result.content:
            text = getattr(block, "text", None)
            if text:
                data = json.loads(text)
                break
    if data is None:
        raise AssertionError(f"工具结果不含可解析内容：{result}")
    if isinstance(data, dict) and set(data) == {"result"}:
        return data["result"]
    return data


def _call_raw(token: str, name: str, arguments: dict | None = None):
    """单次工具调用，返回原始 CallToolResult（供 isError/文本断言）。"""

    async def _inner():
        async with mcp_session(token) as session:
            return await session.call_tool(name, arguments or {})

    return _run(_inner())


def _call_json(token: str, name: str, arguments: dict | None = None):
    return _result_json(_call_raw(token, name, arguments))


# ---------------------------------------------------------------------------
# 用例
# ---------------------------------------------------------------------------


def test_list_tools_contains_all_expected(mcp_dev_server):
    async def _inner():
        async with mcp_session() as session:
            return await session.list_tools()

    listing = _run(_inner())
    names = {t.name for t in listing.tools}
    missing = EXPECTED_TOOLS - names
    assert not missing, f"缺少工具：{sorted(missing)}；实际：{sorted(names)}"


def test_get_snapshot_summary_environments_total_24(mcp_dev_server):
    data = _call_json(ADMIN_TOKEN, "get_snapshot_summary")
    assert data["environments"]["total"] == 24, f"快照环境总数应为 24：{data['environments']}"


def test_list_environments_shows_env_arm_01(mcp_dev_server):
    items = _call_json(ADMIN_TOKEN, "list_environments")
    env = next((e for e in items if e["id"] == "ENV-ARM-01"), None)
    assert env is not None, "ENV-ARM-01 应出现在环境列表中"
    assert env["lifecycle"] == "active"
    assert env["name"] == "ARM64 兼容性环境"


def test_borrow_return_verify_roundtrip(mcp_dev_server):
    """借 ENV-ARM-01（4 小时）→ 归还 → 健康复核释放；借完必须还原现场。"""
    # 预检：环境空闲才执行借用冒烟，被并行任务占用则 skip。
    listing = _call_json(ADMIN_TOKEN, "list_environments")
    env = next((e for e in listing if e["id"] == "ENV-ARM-01"), None)
    assert env is not None, "ENV-ARM-01 应出现在环境列表中"
    if env["status"] != "available":
        pytest.skip(f"ENV-ARM-01 当前状态为 {env['status']}（被并行任务占用），跳过借用冒烟")

    # 借用用途按任务固定为「MCP 冒烟」口径，追加随机后缀仅为绕开服务端
    # 幂等键回放（同 actor+envIds+hours+purpose 会复用上一轮已释放的旧租约单）。
    purpose = f"MCP 冒烟 {uuid.uuid4().hex[:8]}"
    lease_id = None
    finished = False

    async def _flow():
        nonlocal lease_id, finished
        async with mcp_session(ADMIN_TOKEN) as session:
            borrow = await session.call_tool("borrow_environments", {
                "envIds": ["ENV-ARM-01"], "hours": 4, "purpose": purpose})
            assert not borrow.is_error, f"借用失败：{_result_text(borrow)}"
            data = _result_json(borrow)
            outcomes = {o["envId"]: o for o in data["outcomes"]}
            assert outcomes["ENV-ARM-01"]["status"] == "borrowed", outcomes
            lease_id = data["lease"]["id"]
            assert data["lease"]["status"] == "active"
            assert data["lease"]["hours"] == 4
            try:
                ret = await session.call_tool("return_lease", {"lease_id": lease_id})
                assert not ret.is_error, f"归还失败：{_result_text(ret)}"
                assert _result_json(ret)["status"] == "checking"

                verified = await session.call_tool(
                    "verify_return", {"lease_id": lease_id, "healthy": True})
                assert not verified.is_error, f"归还复核失败：{_result_text(verified)}"
                assert _result_json(verified)["status"] == "released"
                finished = True
            finally:
                if not finished:
                    # 清理现场：尽力走完归还+复核，避免占用 dev 库（各自抑制错误）。
                    with contextlib.suppress(Exception):
                        await session.call_tool("return_lease", {"lease_id": lease_id})
                    with contextlib.suppress(Exception):
                        await session.call_tool("verify_return",
                                                {"lease_id": lease_id, "healthy": True})

    _run(_flow())
    assert finished
    # 复核释放后环境应回到可用状态（确认现场已还原）。
    env_after = _call_json(ADMIN_TOKEN, "get_environment", {"env_id": "ENV-ARM-01"})
    assert env_after["status"] == "available", f"释放后应恢复 available：{env_after['status']}"


def test_member_cannot_retire_environment(mcp_dev_server):
    """member 调 retire_environment：工具返回 isError，文本含「仅管理员」。

    已知问题（不在此修复）：SDK 2.x 只把 ToolError/ResourceError 的消息透传给
    客户端；app 抛的 BusinessError 会被遮蔽成 "Error executing tool <name>"，
    导致本用例的文案断言失败——根因在 app/mcp_server.py 的异常转换。
    """
    result = _call_raw(MEMBER_TOKEN, "retire_environment", {"env_id": "ENV-ARM-01"})
    assert result.is_error, f"member 移出环境应报权限错误：{result}"
    assert "仅管理员" in _result_text(result), _result_text(result)


def test_borrow_rejects_invalid_hours(mcp_dev_server):
    """hours 不在 2/4/8/24/48 内：报错文本含「时长」（BusinessError 遮蔽问题同上）。"""
    result = _call_raw(ADMIN_TOKEN, "borrow_environments", {
        "envIds": ["ENV-ARM-01"], "hours": 3, "purpose": "MCP 冒烟"})
    assert result.is_error, f"hours=3 应被拒绝：{result}"
    assert "时长" in _result_text(result), _result_text(result)
