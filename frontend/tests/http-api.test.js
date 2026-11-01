import test from 'node:test'
import assert from 'node:assert/strict'
import { createHttpApi, HttpApiUnavailableError, DEFAULT_IDENTITY } from '../src/api/createHttpApi.js'

function memoryStorage() {
  const values = new Map()
  return { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, String(value)) }
}
function fakeFetch(handler) {
  const calls = []
  const fetchImpl = async (url, options = {}) => { calls.push({ url, options }); return handler(url, options) }
  fetchImpl.calls = calls
  return fetchImpl
}
const json = (body, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => body })
const users = [
  { id: 'U-1001', name: '王磊', role: 'admin', status: 'active', title: '负责人', joined: '2026-01-12' },
  { id: 'U-1002', name: '张明', role: 'member', status: 'active', title: '成员', joined: '2026-02-20' },
  { id: 'U-1003', name: '李四', role: 'member', status: 'disabled', title: '成员', joined: '2026-03-05' },
]
const snapshot = over => ({ schema: 3, revision: 7, currentUserId: null, users, devices: [], environments: [], leases: [], runs: [], maintenance: [], pipelines: [], issues: [], activities: [], ...over })
const okResponse = over => json({ result: over?.result ?? null, snapshot: snapshot(over?.snapshot) })
// GET /api/snapshot 返回裸快照；变更端点返回 { result, snapshot }（与真实后端一致）
const respondDefault = url => (url.endsWith('/api/snapshot') ? json(snapshot()) : okResponse())
const setup = (handler, { base, storage } = {}) => {
  const fetchImpl = fakeFetch(handler || ((url) => respondDefault(url)))
  const api = createHttpApi({ fetch: fetchImpl, storage: storage ?? memoryStorage(), ...(base ? { base } : {}) })
  return { api, calls: fetchImpl.calls }
}
const last = calls => calls[calls.length - 1]
const bodyOf = options => (options.body === undefined ? undefined : JSON.parse(options.body))
const UUID = /^[\da-f]{8}-[\da-f]{4}-[\da-f]{4}-[\da-f]{4}-[\da-f]{12}$/

test('load() GET 默认 base 的 /api/snapshot，带 X-Argus-User，注入 currentUserId 且 persistenceWarning 为 false', async () => {
  const { api, calls } = setup()
  const { snapshot: snap, persistenceWarning } = await api.load()
  assert.equal(calls.length, 1)
  assert.equal(calls[0].url, 'http://127.0.0.1:8000/api/snapshot')
  assert.equal(calls[0].options.method, 'GET')
  assert.equal(calls[0].options.headers['X-Argus-User'], 'U-1001')
  assert.equal(calls[0].options.headers['Content-Type'], undefined)
  assert.equal(persistenceWarning, false)
  assert.equal(snap.schema, 3)
  assert.equal(snap.currentUserId, 'U-1001')
})

test('自定义 base 生效且尾部斜杠被规整', async () => {
  const { api, calls } = setup(() => okResponse(), { base: 'http://127.0.0.1:9000/' })
  await api.load()
  assert.equal(calls[0].url, 'http://127.0.0.1:9000/api/snapshot')
})

test('默认身份与 mock 种子一致，可被导出常量引用', () => {
  assert.equal(DEFAULT_IDENTITY, 'U-1001')
})

test('setIdentity 持久化到 storage（identityKey），identity() 可读，跨实例共享', async () => {
  const store = memoryStorage()
  const { api, calls } = setup(respondDefault, { storage: store })
  assert.equal(api.identity(), 'U-1001')
  assert.equal(api.setIdentity('U-1002'), 'U-1002')
  assert.equal(api.identity(), 'U-1002')
  assert.equal(store.getItem('argus.identity'), 'U-1002')
  await api.load()
  assert.equal(last(calls).options.headers['X-Argus-User'], 'U-1002')
  const reopened = createHttpApi({ fetch: fakeFetch(() => okResponse()), storage: store })
  assert.equal(reopened.identity(), 'U-1002')
})

test('自定义 identityKey 写入对应的键', () => {
  const store = memoryStorage()
  const { api } = setup(() => okResponse(), { storage: store })
  const keyed = createHttpApi({ fetch: fakeFetch(() => okResponse()), storage: store, identityKey: 'argus.identity.test' })
  keyed.setIdentity('U-1002')
  assert.equal(store.getItem('argus.identity.test'), 'U-1002')
  assert.equal(api.identity(), 'U-1001') // 不同 identityKey 互不影响
})

test('borrow 归一化 envId 为 envIds，携带 JSON 头与每次不同的幂等键，result 整包透传', async () => {
  const { api, calls } = setup(() => okResponse({ result: { lease: { id: 'LEASE-2001' }, outcomes: [], requestedEnvIds: ['ENV-ARM-01'] } }))
  const first = await api.borrow({ envId: 'ENV-ARM-01', hours: 4, purpose: '联调' })
  assert.equal(first.result.lease.id, 'LEASE-2001')
  assert.equal(first.snapshot.currentUserId, 'U-1001')
  let options = last(calls).options
  assert.equal(last(calls).url, 'http://127.0.0.1:8000/api/leases/batch')
  assert.equal(options.method, 'POST')
  assert.deepEqual(bodyOf(options), { envIds: ['ENV-ARM-01'], hours: 4, purpose: '联调' })
  assert.equal(options.headers['Content-Type'], 'application/json')
  assert.match(options.headers['X-Argus-Request-Key'], UUID)
  await api.borrow({ envIds: ['ENV-VM-03', 'ENV-VM-04'], hours: 8, purpose: '批量' })
  const keys = [calls[0].options.headers['X-Argus-Request-Key'], calls[1].options.headers['X-Argus-Request-Key']]
  assert.match(keys[1], UUID)
  assert.notEqual(keys[0], keys[1])
  assert.deepEqual(bodyOf(calls[1].options), { envIds: ['ENV-VM-03', 'ENV-VM-04'], hours: 8, purpose: '批量' })
})

test('renew 默认续 4 小时，显式 hours 透传', async () => {
  const { api, calls } = setup(url => { assert.ok(url.endsWith('/api/leases/LEASE-2001/renew')); return okResponse() })
  await api.renew('LEASE-2001')
  assert.deepEqual(bodyOf(last(calls).options), { hours: 4 })
  await api.renew('LEASE-2001', 8)
  assert.deepEqual(bodyOf(last(calls).options), { hours: 8 })
})

test('归还复核、维护复核与任务终态/清理的布尔参数进入请求体', async () => {
  const { api, calls } = setup(() => okResponse())
  await api.verifyReturn('LEASE-2001', false)
  await api.verifyMaintenance('MT-003', true)
  await api.finishRun('RUN-1025', false)
  await api.cleanupRun('RUN-1025', true)
  assert.deepEqual(calls.map(call => call.url), [
    'http://127.0.0.1:8000/api/leases/LEASE-2001/verify-return',
    'http://127.0.0.1:8000/api/maintenance/MT-003/verify',
    'http://127.0.0.1:8000/api/runs/RUN-1025/finish',
    'http://127.0.0.1:8000/api/runs/RUN-1025/cleanup',
  ])
  assert.deepEqual(calls.map(call => bodyOf(call.options)), [{ healthy: false }, { healthy: true }, { success: false }, { healthy: true }])
})

test('无请求体的操作（reset/retire/return）不发 body 也不带 JSON 头', async () => {
  const { api, calls } = setup(() => okResponse({ result: { reset: true } }))
  const reset = await api.reset()
  assert.equal(reset.result.reset, true)
  await api.archiveEnvironment('ENV-ARM-01')
  await api.retireDevice('DEV-001')
  await api.requestReturn('LEASE-2001')
  assert.deepEqual(calls.map(call => call.url), [
    'http://127.0.0.1:8000/api/reset',
    'http://127.0.0.1:8000/api/environments/ENV-ARM-01/retire',
    'http://127.0.0.1:8000/api/devices/DEV-001/retire',
    'http://127.0.0.1:8000/api/leases/LEASE-2001/return',
  ])
  for (const { options } of calls) {
    assert.equal(options.method, 'POST')
    assert.equal(options.body, undefined)
    assert.equal(options.headers['Content-Type'], undefined)
    assert.equal(options.headers['X-Argus-User'], 'U-1001')
  }
})

test('资源与人员操作的 URL 和 payload 逐项对齐后端契约', async () => {
  const { api, calls } = setup(() => okResponse())
  await api.saveEnvironment({ id: 'ENV-NEW', name: '新环境', arch: 'ARM64', type: 'physical', deviceIds: ['DEV-001'], description: '', version: '', location: '', owner: 'U-1001', tags: [] })
  await api.saveDevice({ id: 'DEV-NEW', name: '新设备', arch: 'ARM64', type: 'physical', cpu: '8C', memory: '32G', ip: '10.0.0.9', location: 'A栋' })
  await api.startRun({ pipelineId: 'os-smoke-test', envId: 'ENV-VM-03' })
  await api.startMaintenance({ envId: 'ENV-VM-03', reason: '磁盘更换' })
  await api.addUser({ name: '赵六' })
  await api.setUserRole('U-1002', 'admin')
  await api.setUserStatus('U-1002', 'disabled')
  assert.deepEqual(calls.map(call => call.url), [
    'http://127.0.0.1:8000/api/environments',
    'http://127.0.0.1:8000/api/devices',
    'http://127.0.0.1:8000/api/runs',
    'http://127.0.0.1:8000/api/maintenance',
    'http://127.0.0.1:8000/api/users',
    'http://127.0.0.1:8000/api/users/U-1002/role',
    'http://127.0.0.1:8000/api/users/U-1002/status',
  ])
  assert.deepEqual(calls.map(call => bodyOf(call.options)), [
    { id: 'ENV-NEW', name: '新环境', arch: 'ARM64', type: 'physical', deviceIds: ['DEV-001'], description: '', version: '', location: '', owner: 'U-1001', tags: [] },
    { id: 'DEV-NEW', name: '新设备', arch: 'ARM64', type: 'physical', cpu: '8C', memory: '32G', ip: '10.0.0.9', location: 'A栋' },
    { pipelineId: 'os-smoke-test', envId: 'ENV-VM-03' },
    { envId: 'ENV-VM-03', reason: '磁盘更换' },
    { name: '赵六', role: 'member' },
    { role: 'admin' },
    { status: 'disabled' },
  ])
})

test('变更响应 { result, snapshot } 整包透传，仅注入 currentUserId', async () => {
  const { api, calls } = setup(() => json({ result: { id: 'MT-004' }, snapshot: snapshot({ revision: 9 }) }))
  const { result, snapshot: snap } = await api.verifyMaintenance('MT-003', true)
  assert.deepEqual(result, { id: 'MT-004' })
  assert.equal(snap.revision, 9)
  assert.equal(snap.currentUserId, 'U-1001')
  assert.equal(calls.length, 1)
})

test('非 2xx 抛出后端 detail 文案并挂 code 与 status（403 权限 / 401 身份）', async () => {
  const forbidden = setup(() => json({ detail: '重置演示数据仅管理员可执行', code: 'permission_denied' }, 403))
  await assert.rejects(forbidden.api.reset(), error => {
    assert.equal(error.message, '重置演示数据仅管理员可执行')
    assert.equal(error.code, 'permission_denied')
    assert.equal(error.status, 403)
    assert.equal(error.unavailable, undefined)
    return true
  })
  const unknown = setup(() => json({ detail: '身份不存在或已失效，请刷新后重试。', code: 'unknown_actor' }, 401))
  await assert.rejects(unknown.api.load(), /身份不存在或已失效/)
  const business = setup(() => json({ detail: '请选择有效的借用时长。', code: 'business_rule' }, 400))
  await assert.rejects(business.api.borrow({ envIds: ['ENV-VM-03'], hours: 3, purpose: 'x' }), /请选择有效的借用时长/)
})

test('错误响应体非 JSON 时回退为带状态码的通用文案', async () => {
  const { api } = setup(() => ({ ok: false, status: 502, json: async () => { throw new Error('bad gateway html') } }))
  await assert.rejects(api.load(), error => {
    assert.equal(error.message, '请求失败（HTTP 502）')
    assert.equal(error.code, undefined)
    assert.equal(error.status, 502)
    return true
  })
})

test('网络层失败时 load() 抛出带 unavailable 标记的 HttpApiUnavailableError', async () => {
  const { api } = setup(() => { throw new TypeError('fetch failed') })
  await assert.rejects(api.load(), error => {
    assert.ok(error instanceof HttpApiUnavailableError)
    assert.equal(error.unavailable, true)
    assert.equal(error.code, 'network')
    assert.match(error.message, /无法连接/)
    return true
  })
  await assert.rejects(api.borrow({ envId: 'ENV-VM-03', hours: 4, purpose: '离线' }), HttpApiUnavailableError)
})

test('switchUser 等价 setIdentity + 重新 load，返回 { snapshot, result: user }', async () => {
  const { api, calls } = setup(respondDefault)
  const switched = await api.switchUser('U-1002')
  assert.equal(switched.result.id, 'U-1002')
  assert.equal(switched.snapshot.currentUserId, 'U-1002')
  assert.equal(api.identity(), 'U-1002')
  assert.equal(calls.length, 1)
  assert.equal(calls[0].options.method, 'GET')
  await api.load()
  assert.equal(last(calls).options.headers['X-Argus-User'], 'U-1002')
})

test('switchUser 拒绝当前身份（不发请求）与停用人员（回滚身份）', async () => {
  const { api, calls } = setup(respondDefault)
  await assert.rejects(api.switchUser('U-1001'), /当前已是此身份/)
  assert.equal(calls.length, 0)
  await assert.rejects(api.switchUser('U-1003'), /停用/)
  assert.equal(api.identity(), 'U-1001')
  assert.equal(calls.length, 1)
  await api.load()
  assert.equal(last(calls).options.headers['X-Argus-User'], 'U-1001')
})

test('switchUser 期间后端不可达：抛 unavailable 并回滚身份，恢复后可重试', async () => {
  let online = false
  const { api } = setup(url => { if (!online) throw new TypeError('fetch failed'); return respondDefault(url) })
  await assert.rejects(api.switchUser('U-1002'), HttpApiUnavailableError)
  assert.equal(api.identity(), 'U-1001')
  online = true
  const switched = await api.switchUser('U-1002')
  assert.equal(switched.result.id, 'U-1002')
})

test('storage 被禁时身份退化为内存兜底，不再抛错', async () => {
  const denied = { getItem() { throw new Error('denied') }, setItem() { throw new Error('denied') } }
  const { api, calls } = setup(() => okResponse(), { storage: denied })
  assert.equal(api.identity(), 'U-1001')
  api.setIdentity('U-1002')
  assert.equal(api.identity(), 'U-1002')
  await api.load()
  assert.equal(last(calls).options.headers['X-Argus-User'], 'U-1002')
  const ephemeral = createHttpApi({ fetch: fakeFetch(() => okResponse()), storage: null })
  assert.equal(ephemeral.identity(), 'U-1001')
  ephemeral.setIdentity('U-1002')
  assert.equal(ephemeral.identity(), 'U-1002')
})

test('查询扩展 events / listEnvironments / distribution 拼接查询参数且返回原始载荷', async () => {
  const { api, calls } = setup(() => json({ events: [], environments: [] }))
  await api.events({ entityType: 'lease', entityId: 'LEASE-2001', limit: 10, beforeId: 42 })
  await api.events()
  await api.listEnvironments({ arch: 'ARM64', includeRetired: true, query: '存储' })
  await api.listEnvironments()
  await api.distribution()
  assert.deepEqual(calls.map(call => call.url), [
    'http://127.0.0.1:8000/api/events?entity_type=lease&entity_id=LEASE-2001&limit=10&before_id=42',
    'http://127.0.0.1:8000/api/events',
    `http://127.0.0.1:8000/api/environments?include_retired=true&arch=ARM64&query=${encodeURIComponent('存储')}`,
    'http://127.0.0.1:8000/api/environments',
    'http://127.0.0.1:8000/api/reports/distribution',
  ])
  for (const { options } of calls) assert.equal(options.headers['X-Argus-User'], 'U-1001')
})
