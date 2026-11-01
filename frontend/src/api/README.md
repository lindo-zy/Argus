# createHttpApi — HTTP 适配层接线说明

`createHttpApi()` 与 `../mock/api.js` 的 `createMockApi()` **同接口**：方法名、参数签名、返回值一致（变更方法都 resolve `{ result, snapshot }`，`load()` resolve `{ snapshot, persistenceWarning }`）。数据由 FastAPI 后端整包返回，本层只做传输、身份与错误规整。对接契约见 `backend/app/api.py`。

## App.vue 接线（3 行核心）

```js
let api = createHttpApi()                                   // 替换现第 25 行 const api = createMockApi({ storage })
const mockApi = createMockApi({ storage })                  // 后端离线时的回退实例（共用同一 storage）
// App.vue 现有 load() 的 catch 里加一行：if (error?.unavailable && api !== mockApi) { api = mockApi; return load() }
```

要点：

- **只有 `error.unavailable === true`（`HttpApiUnavailableError`，网络层 fetch reject）才回退 mock**；4xx 业务错误照常走 `loadError` 提示，不能回退（会掩盖后端真实状态）。
- 回退后两套实现接口一致，`load`/`action`/所有 `api.*` 调用点都无需再改。
- 回退语义是「启动时后端不在」的演示兜底：mock 用的是 localStorage 演示数据，与后端库无关。会话中途后端挂掉时，变更请求只提示 `无法连接 Argus 后端服务…`（`action()` 的 modalError 已覆盖），**不要**自动切 mock，避免数据分叉。
- CORS：后端只允许 `http://127.0.0.1:5173` / `http://localhost:5173`（`backend/app/main.py`），`vite` dev 默认端口即 5173。

## 签名

```js
createHttpApi({
  base = 'http://127.0.0.1:8000',
  fetch = globalThis.fetch.bind(globalThis),   // 测试可注入假 fetch
  identityKey = 'argus.identity',              // 身份在 localStorage 的键
  storage = 浏览器 localStorage,                // Node/测试可注入内存实现；取不到时身份走内存
} = {})
```

方法集与 mock 完全对齐（19 个），另有 HTTP 专属方法：`identity()`、`setIdentity(userId)`，以及查询扩展 `events()` / `distribution()` / `listEnvironments()`（返回后端原始载荷，非 `{ result, snapshot }`）。

## 方法 → 端点映射

| mock 方法 | HTTP |
| --- | --- |
| `load()` | `GET /api/snapshot` |
| `reset()` | `POST /api/reset`（服务端按身份校验管理员） |
| `borrow({ envIds, envId, hours, purpose })` | `POST /api/leases/batch`；单选 `envId` 归一化为 `envIds`；自动带 `X-Argus-Request-Key` 幂等键 |
| `saveEnvironment(payload)` | `POST /api/environments` |
| `saveDevice(payload)` | `POST /api/devices` |
| `archiveEnvironment(id)` | `POST /api/environments/{id}/retire` |
| `retireDevice(id)` | `POST /api/devices/{id}/retire` |
| `renew(id, hours = 4)` | `POST /api/leases/{id}/renew` `{ hours }` |
| `requestReturn(id)` | `POST /api/leases/{id}/return` |
| `verifyReturn(id, healthy)` | `POST /api/leases/{id}/verify-return` `{ healthy }` |
| `startMaintenance({ envId, reason })` | `POST /api/maintenance` |
| `verifyMaintenance(id, healthy)` | `POST /api/maintenance/{id}/verify` `{ healthy }` |
| `startRun({ pipelineId, envId })` | `POST /api/runs` |
| `finishRun(id, success = true)` | `POST /api/runs/{id}/finish` `{ success }` |
| `cleanupRun(id, healthy)` | `POST /api/runs/{id}/cleanup` `{ healthy }` |
| `addUser({ name, role = 'member' })` | `POST /api/users` |
| `setUserRole(id, role)` | `POST /api/users/{id}/role` `{ role }` |
| `setUserStatus(id, status)` | `POST /api/users/{id}/status` `{ status }` |
| `switchUser(id)` | `setIdentity(id)` + 重新 `load()`（见下） |

查询扩展：`events({ entityType, entityId, limit, beforeId })` → `GET /api/events`；`distribution()` → `GET /api/reports/distribution`；`listEnvironments({ includeRetired, arch, type, tag, owner, query })` → `GET /api/environments`。

## 行为约定

- **身份是前端事实**：每个请求带 `X-Argus-User: <userId>` 头；身份存 `localStorage[identityKey]`，未设置时回退 `DEFAULT_IDENTITY`（`U-1001`，与 mock 种子 `DEFAULT_USER_ID` 一致，避免冷启动 401）。`identity()` 读取、`setIdentity(userId)` 写入。
- **currentUserId 注入**：后端快照的 `currentUserId` 固定为 `null`（`backend/app/services/snapshot.py`），本层在 `load()` 与每个变更响应里按当前身份注入 `snapshot.currentUserId`——App.vue 的 `currentUser`/`isAdmin` 计算属性无需任何改动。
- **switchUser**：HTTP 模式下身份在前端，实现为 `setIdentity(id)` + 重新 `load()`，返回 `{ snapshot, result: user }`（user 从新快照里找，App.vue 现有提示文案可直接用）。与 mock 同样拒绝「当前已是此身份」「停用人员」，失败时回滚身份再抛错。
- **borrow 幂等**：每次 borrow 调用生成 `crypto.randomUUID()`（非安全上下文退化为时间戳随机串）作为 `X-Argus-Request-Key`，同一次借用的网络重试不会重复建租约。
- **load() 回退标记**：网络层失败抛 `HttpApiUnavailableError`（`error.unavailable === true`、`error.code === 'network'`），即上文 3 行接线的回退依据；`persistenceWarning` 恒为 `false`（数据在服务端），App.vue 现有的提示分支不会触发。
- **错误规整**：非 2xx 解析 `{ detail, code }`，`throw new Error(detail)` 并挂 `error.code`（`business_rule`=400 业务拒绝、`unknown_actor`=401 身份无效、`permission_denied`=403 权限）与 `error.status`；响应体非 JSON 时回退为 `请求失败（HTTP <status>）`。
- **快照透传**：变更响应 `{ result, snapshot }` 整包透传，不再二次 GET。

## 测试

```bash
cd frontend && node --test tests/http-api.test.js   # 19 项，注入假 fetch，不发真请求
npm test                                            # 全量（含既有 mock 31 项）
```
