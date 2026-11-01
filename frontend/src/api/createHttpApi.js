// HTTP 适配层：与 src/mock/api.js 的 createMockApi 同接口（方法名、签名、返回值一致），
// 对接 FastAPI 后端。快照与变更结果由后端整包返回 { result, snapshot }，本层只做传输、身份与错误规整。
// 身份是前端事实：后端快照的 currentUserId 固定为 null（见 backend/app/services/snapshot.py），
// 由本层按当前身份注入，App.vue 的 currentUser/isAdmin 计算属性无需改动。

const FALLBACK_IDENTITY = 'U-1001' // 与 mock/seed.js 的 DEFAULT_USER_ID 一致，冷启动不发空身份

export const DEFAULT_IDENTITY = FALLBACK_IDENTITY

// 特殊标记错误：仅网络层失败（fetch reject）时抛出，上层据此在启动时回退 mock。
// 业务拒绝（4xx）走普通 Error(detail)，绝不能用这个类。
export class HttpApiUnavailableError extends Error {
  constructor(cause) {
    super('无法连接 Argus 后端服务，请确认服务已启动。')
    this.name = 'HttpApiUnavailableError'
    this.unavailable = true
    this.code = 'network'
    this.cause = cause
  }
}

function defaultStorage() {
  // 浏览器用 localStorage；Node/测试环境（无 localStorage 或访问被禁）返回 null 走内存。
  try { return typeof localStorage === 'undefined' ? null : localStorage } catch { return null }
}

function newRequestKey() {
  const uuid = globalThis.crypto?.randomUUID?.()
  // 非安全上下文（局域网 http）没有 crypto.randomUUID，退化为时间戳随机串，保证同调用重试幂等即可。
  return uuid || `req-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}

export function createHttpApi({
  base = 'http://127.0.0.1:8000',
  fetch: fetchImpl = globalThis.fetch.bind(globalThis),
  identityKey = 'argus.identity',
  storage = defaultStorage(),
} = {}) {
  const root = String(base).replace(/\/+$/, '')
  let memoryIdentity = ''
  function readIdentity() {
    try { return storage?.getItem(identityKey) ?? '' } catch { return '' }
  }
  function writeIdentity(value) {
    memoryIdentity = value
    try { storage?.setItem(identityKey, value) } catch { /* 存储被禁时留在内存 */ }
  }
  function identity() {
    return readIdentity() || memoryIdentity || FALLBACK_IDENTITY
  }
  function setIdentity(userId) {
    writeIdentity(String(userId ?? '').trim())
    return identity()
  }
  async function request(path, { method = 'GET', body, headers } = {}) {
    let response
    try {
      response = await fetchImpl(`${root}${path}`, {
        method,
        headers: { 'X-Argus-User': identity(), ...(body === undefined ? {} : { 'Content-Type': 'application/json' }), ...headers },
        body: body === undefined ? undefined : JSON.stringify(body),
      })
    } catch (cause) { throw new HttpApiUnavailableError(cause) }
    let payload = null
    try { payload = await response.json() } catch { payload = null } // 204 或非 JSON 错误页
    if (!response.ok) {
      const detail = payload?.detail
      const error = new Error(typeof detail === 'string' && detail ? detail : `请求失败（HTTP ${response.status}）`)
      if (payload?.code) error.code = payload.code
      error.status = response.status
      throw error
    }
    return payload
  }
  function withActor(snap) {
    if (snap && typeof snap === 'object') snap.currentUserId = identity()
    return snap
  }
  async function mutate(path, { body, requestKey } = {}) {
    const data = await request(path, {
      method: 'POST',
      body,
      headers: requestKey ? { 'X-Argus-Request-Key': requestKey } : undefined,
    })
    withActor(data?.snapshot)
    return data
  }
  async function load() {
    return { snapshot: withActor(await request('/api/snapshot')), persistenceWarning: false }
  }
  async function switchUser(userId) {
    const target = String(userId ?? '').trim()
    if (!target) throw new Error('人员不存在，请刷新后重试。')
    if (target === identity()) throw new Error('当前已是此身份。')
    const previous = identity()
    setIdentity(target)
    try {
      const { snapshot } = await load()
      const user = (snapshot?.users || []).find(item => item.id === target)
      if (!user) throw new Error('人员不存在，请刷新后重试。')
      if (user.status !== 'active') throw new Error('该人员已停用，不能切换为此身份。')
      return { snapshot, result: user }
    } catch (error) {
      setIdentity(previous) // 切换失败回滚身份，后续请求仍用原身份
      throw error
    }
  }
  function buildQuery(entries) {
    const query = entries.filter(([, value]) => value !== '' && value !== 0 && value !== false)
      .map(([key, value]) => `${key}=${encodeURIComponent(value)}`).join('&')
    return query ? `?${query}` : ''
  }
  return {
    load,
    reset: () => mutate('/api/reset'),
    borrow({ envIds, envId, hours, purpose } = {}) {
      // 与 mock 一致：单选 envId 归一化为 envIds；每次调用生成新幂等键（X-Argus-Request-Key），
      // 网络重试同一次 borrow 不会重复建租约。
      const requestedEnvIds = envIds === undefined ? (envId ? [envId] : []) : envIds
      return mutate('/api/leases/batch', { body: { envIds: requestedEnvIds, hours, purpose }, requestKey: newRequestKey() })
    },
    saveEnvironment: payload => mutate('/api/environments', { body: payload }),
    saveDevice: payload => mutate('/api/devices', { body: payload }),
    archiveEnvironment: id => mutate(`/api/environments/${encodeURIComponent(id)}/retire`),
    retireDevice: id => mutate(`/api/devices/${encodeURIComponent(id)}/retire`),
    renew: (id, hours = 4) => mutate(`/api/leases/${encodeURIComponent(id)}/renew`, { body: { hours } }),
    requestReturn: id => mutate(`/api/leases/${encodeURIComponent(id)}/return`),
    verifyReturn: (id, healthy) => mutate(`/api/leases/${encodeURIComponent(id)}/verify-return`, { body: { healthy } }),
    startMaintenance: ({ envId, reason } = {}) => mutate('/api/maintenance', { body: { envId, reason } }),
    verifyMaintenance: (id, healthy) => mutate(`/api/maintenance/${encodeURIComponent(id)}/verify`, { body: { healthy } }),
    startRun: ({ pipelineId, envId } = {}) => mutate('/api/runs', { body: { pipelineId, envId } }),
    finishRun: (id, success = true) => mutate(`/api/runs/${encodeURIComponent(id)}/finish`, { body: { success } }),
    cleanupRun: (id, healthy) => mutate(`/api/runs/${encodeURIComponent(id)}/cleanup`, { body: { healthy } }),
    addUser: ({ name, role = 'member' } = {}) => mutate('/api/users', { body: { name, role } }),
    setUserRole: (id, role) => mutate(`/api/users/${encodeURIComponent(id)}/role`, { body: { role } }),
    setUserStatus: (id, status) => mutate(`/api/users/${encodeURIComponent(id)}/status`, { body: { status } }),
    switchUser,
    // HTTP 模式专属：身份读取与切换（存 localStorage 的 identityKey，缺省回退 DEFAULT_IDENTITY）
    identity,
    setIdentity,
    // 查询扩展（mock 无对应方法）：返回后端原始载荷，不是 { result, snapshot }
    events: ({ entityType = '', entityId = '', limit, beforeId = 0 } = {}) =>
      request(`/api/events${buildQuery([['entity_type', entityType], ['entity_id', entityId], ['limit', limit ?? 0], ['before_id', beforeId]])}`),
    distribution: () => request('/api/reports/distribution'),
    listEnvironments: ({ includeRetired = false, arch = '', type = '', tag = '', owner = '', location = '', version = '', query = '' } = {}) =>
      request(`/api/environments${buildQuery([['include_retired', includeRetired], ['arch', arch], ['type', type], ['tag', tag], ['owner', owner], ['location', location], ['version', version], ['query', query]])}`),
    utilization: (envId, days = 7) =>
      request(`/api/environments/${encodeURIComponent(envId)}/utilization${buildQuery([['days', days]])}`),
    exportUrl: ({ includeRetired = false, arch = '', type = '', tag = '', owner = '', location = '', version = '', query = '' } = {}) =>
      `${root}/api/export/environments.csv${buildQuery([['include_retired', includeRetired], ['arch', arch], ['type', type], ['tag', tag], ['owner', owner], ['location', location], ['version', version], ['query', query]])}`,
  }
}
