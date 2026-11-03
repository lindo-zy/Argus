// 升级流水线 / 性能流水线 / 性能数据看板三个规划页共用的浏览器演示内核。
// 与 mock/api.js 同约定：load() 返回 { snapshot, persistenceWarning }，变更方法返回 { snapshot, result }，
// 校验失败以 Promise 普通拒绝抛出错误。数据只落本浏览器 localStorage，缺省键时退回内存演示。
// 这些 store 刻意不进入主快照（argus.mock.v1）与后端契约：规划页当前仅做 Mock 实现（见 frontend/README.md）。

const clone = value => JSON.parse(JSON.stringify(value))
const wait = ms => new Promise(resolve => setTimeout(resolve, ms))

// 本地时间 'YYYY-MM-DD HH:mm'（零填充，保证字符串排序与时间顺序一致，与历史样本格式相同）。
export function localStamp(date) {
  const pad = number => String(number).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

export function defaultDemoStorage() {
  try { return typeof localStorage === 'undefined' ? null : localStorage } catch { return null }
}

export function createDemoStore({ key, storage = null, latency = 240, now = () => new Date(), seed }) {
  let memory = seed()
  let persistenceWarning = false
  const arrayKeys = () => Object.entries(memory).filter(([, value]) => Array.isArray(value)).map(([name]) => name)
  function read() {
    try {
      const raw = storage?.getItem(key)
      if (raw) {
        const parsed = JSON.parse(raw)
        if (!parsed || typeof parsed !== 'object' || parsed.schema !== memory.schema
          || !arrayKeys().every(name => Array.isArray(parsed[name]))) throw new Error('Invalid demo snapshot')
        memory = parsed
      }
    } catch { persistenceWarning = true }
    return clone(memory)
  }
  function save(data) {
    memory = clone(data)
    try { storage?.setItem(key, JSON.stringify(data)) } catch { persistenceWarning = true }
  }
  function activity(data, text, tone = 'blue') {
    data.activities.unshift({ time: now().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }), text, tone })
    data.activities = data.activities.slice(0, 12)
  }
  async function mutate(action, { shouldCommit = () => true } = {}) {
    await wait(latency)
    const data = read()
    const result = action(data)
    if (shouldCommit(result ?? true)) { data.revision++; save(data) }
    return { snapshot: clone(data), result: clone(result ?? null) }
  }
  function nextId(prefix, rows) {
    return `${prefix}-${Math.max(2000, ...rows.map(row => Number(row.id.split('-').at(-1)) || 0)) + 1}`
  }
  function requireAdmin(actor, what) {
    if (!actor || typeof actor !== 'object') throw new Error('缺少操作身份，请刷新页面后重试。')
    if (actor.role !== 'admin') throw new Error(`${what}仅管理员可执行，当前身份「${actor.name || '未知'}」为普通人员。`)
    return actor
  }
  return {
    load: async () => { await wait(latency); return { snapshot: read(), persistenceWarning } },
    mutate,
    activity,
    nextId,
    requireAdmin,
    persistenceWarning: () => persistenceWarning,
  }
}
