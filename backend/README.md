# Argus Backend

REST `/api` + MCP `/mcp` 双入口 → 同一领域服务层（`app/services/`）→ PostgreSQL（12 张业务表 + `users`，另有 `workspaces`、`background_tasks` 辅助表）。

## 本地启动

```bash
# 1. 数据库（docker-compose 里的 postgres:17，映射 127.0.0.1:5433，库 argus / 用户 argus / 密码 argus）
docker-compose up -d db

# 2. 建表
cd backend && .venv/bin/alembic upgrade head

# 3. 启动开发服务（首次连空库时自动 seed：24 环境 / 58 设备 / 4 用户 / 3 流水线与演示状态）
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

- 数据库连接由 `ARGUS_DATABASE_URL` 覆盖，缺省 `postgresql+psycopg://argus:argus@127.0.0.1:5433/argus`。
- 依赖安装：`python3.12 -m venv .venv && .venv/bin/pip install -e '.[dev]'`。
- 恢复演示样本：`POST /api/reset`。

## 身份模型（演示级）

- REST：请求头 `X-Argus-User: <用户ID>`。
- MCP：请求头 `Authorization: Bearer <api_token>`，token → 用户映射存 `users.api_token`；MCP 侧无有效 token 时回退演示管理员。
- 删除类操作（移出/恢复）与人员管理仅 admin 角色；真实身份源为后续调研项（docs/05）。

| Token | 用户 | 角色 |
| --- | --- | --- |
| `argus-demo-admin` | U-1001 王磊 | admin |
| `argus-token-zhangming` | U-1002 张明 | member |
| `argus-token-lisi` | U-1003 李四 | member |
| `argus-token-chenjing` | U-1004 陈静 | member |

## MCP 接入

Streamable HTTP 入口挂载于 `http://127.0.0.1:8000/mcp`。ZCode / Claude 等 MCP host 的配置：

```json
{
  "mcpServers": {
    "argus": {
      "url": "http://127.0.0.1:8000/mcp",
      "headers": {
        "Authorization": "Bearer argus-demo-admin"
      }
    }
  }
}
```

工具清单（`app/mcp_server.py`）：

| 工具 | 说明 |
| --- | --- |
| `list_environments` | 环境列表：按架构、类型、标签、负责人、关键字筛选 |
| `get_environment` | 单环境详情：基础信息、关联设备、派生状态 |
| `get_environment_timeline` | 环境历史时间线（借用/执行/维护/事件） |
| `save_environment` | 新增或更新环境（自助，需 name/arch/type/deviceIds） |
| `adjust_environment_devices` | 调整环境设备关联（add/remove，占用设备不可摘除） |
| `retire_environment` | 移出环境（仅管理员，保留历史可恢复） |
| `restore_environment` | 恢复已移出环境（仅管理员，重验设备引用与占用） |
| `check_environment_health` | 对空闲环境发起手动健康检查 |
| `list_devices` | 设备列表（含派生状态） |
| `create_device` | 新增设备（自助） |
| `borrow_environments` | 批量借用：hours ∈ 2/4/8/24/48，purpose 必填，逐项部分成功 |
| `return_lease` | 归还自己的借用单（进入健康复核，不立即释放） |
| `verify_return` | 归还健康复核：healthy=true 释放设备，false 整单隔离 |
| `start_maintenance` | 对空闲环境发起维护 |
| `verify_maintenance` | 维护健康复核：healthy=true 恢复可用 |
| `get_snapshot_summary` | 工作空间概览：环境/设备计数、待办、最近动态 |
| `list_events` | 资源事件流（审计），可按实体类型/ID 过滤 |
| `start_demo_run` | 启动演示执行任务（需已发布流水线版本 + 匹配架构空闲环境） |

### 已知问题（MCP 通路当前不可用，修复前先看这里）

1. **挂载缺陷**：`app.main` 用 `app.mount("/mcp", mcp_app())`，而 SDK 2.x 子应用内部注册的路径是 `/mcp`，实际端点变成 `/mcp/mcp`；且 FastAPI 不会运行被挂载子应用的 lifespan，session manager 未 `run()`，请求一律 500（`RuntimeError: Task group is not initialized`）。修复方向：在 FastAPI lifespan 中 `async with mcp_app().session_manager.run():`，并让子应用以根路径 `/` 注册（`streamable_http_path="/"`)。
2. **业务错误文案被遮蔽**：SDK 2.x 只把 `ToolError` / `ResourceError` 的消息透传给客户端，工具抛 `BusinessError` 时客户端只能看到 `Error executing tool <name>`（is_error=True 但无业务文案）。修复方向：`mcp_server._run` 在抛出前把 `BusinessError` 转成 `ToolError`。

## 常用 curl

```bash
# 工作空间快照（环境/设备计数与状态分布）
curl -H 'X-Argus-User: U-1001' http://127.0.0.1:8000/api/snapshot

# 批量借用（member 李四借 ENV-ARM-01 四小时）
curl -X POST http://127.0.0.1:8000/api/leases/batch \
  -H 'X-Argus-User: U-1003' -H 'Content-Type: application/json' \
  -d '{"envIds":["ENV-ARM-01"],"hours":4,"purpose":"冒烟演示"}'
# 归还两步：POST /api/leases/{leaseId}/return → POST /api/leases/{leaseId}/verify-return（body {"healthy": true}）

# 资源事件流（审计，倒序）
curl -H 'X-Argus-User: U-1001' 'http://127.0.0.1:8000/api/events?limit=20'
```

## 测试

```bash
cd backend

# MCP 端到端冒烟：直连 8000 开发服务与 dev 库，需先按上文启动 uvicorn。
# 借用用例借完即还原；ENV-ARM-01 被并行任务占用或服务未启动时自动 skip。
.venv/bin/pytest tests/test_mcp.py -v

# 全套 pytest（REST/服务层用例走 tests/conftest.py 的独立测试库，每用例 TRUNCATE + 重新 seed）
# 首次需先建测试库：
docker exec argus-db psql -U argus -c 'CREATE DATABASE argus_test'
.venv/bin/pytest -v
```

- MCP 冒烟默认目标 `http://127.0.0.1:8000/mcp`，可用环境变量 `ARGUS_MCP_URL` 覆盖（例如修好挂载前临时指向有效端点）。
- 借用工具有幂等键回放（同 actor+envIds+hours+purpose 复用旧结果），冒烟用例对 purpose 追加随机后缀以支持反复运行。
