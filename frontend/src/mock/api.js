import { createSeed, DEFAULT_USER_ID, DATA_KEY, environmentIdsFor, deviceIdsFor } from './seed.js'
import { writeEnvironment, writeDevice, archiveEnvironment, retireDevice, borrowFailure } from './resources.js'

const clone = value => JSON.parse(JSON.stringify(value))
const wait = ms => new Promise(resolve => setTimeout(resolve, ms))

// This entire module executes in the browser. No server, request interception or backend code.
// Keep call sites asynchronous so a future HTTP adapter can replace this service boundary.
export function createMockApi({ storage = null, latency = 240, now = () => new Date() } = {}) {
  let memory = createSeed()
  let persistenceWarning = false
  function read() {
    try {
      const raw = storage?.getItem(DATA_KEY)
      if (raw) {
        const parsed = JSON.parse(raw)
        // Migrate pre-permission snapshots (schema 1) by injecting the seeded personnel roster.
        if (parsed.schema === 1) { parsed.schema = 2; parsed.users = createSeed().users; parsed.currentUserId = DEFAULT_USER_ID }
        const collections = ['users', 'devices', 'environments', 'leases', 'runs', 'maintenance', 'pipelines', 'issues', 'activities']
        if (parsed.schema !== 2 || !collections.every(key => Array.isArray(parsed[key]))) throw new Error('Invalid snapshot')
        memory = parsed
      }
    } catch { persistenceWarning = true }
    return clone(memory)
  }
  function save(data) {
    memory = clone(data)
    try { storage?.setItem(DATA_KEY, JSON.stringify(data)) } catch { persistenceWarning = true }
  }
  function activity(data, text, tone = 'blue') {
    data.activities.unshift({ time: now().toLocaleTimeString('zh-CN', { timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit' }), text, tone })
    data.activities = data.activities.slice(0, 12)
  }
  async function mutate(action, { shouldCommit = () => true } = {}) {
    await wait(latency)
    // Re-read and apply synchronously after the delay, so concurrent clicks recheck the latest occupancy.
    const data = read()
    const result = action(data)
    if (shouldCommit(result)) { data.revision++; save(data) }
    return { snapshot: clone(data), result: clone(result ?? null) }
  }
  function getEnv(data, id) {
    const env = data.environments.find(e => e.id === id && e.lifecycle !== 'retired')
    if (!env) throw new Error('环境不存在，请刷新后重试。')
    return env
  }
  function available(data, env) {
    const failure = borrowFailure(data, env)
    if (failure) throw new Error(`${env.name}：${failure.reason}。`)
  }
  function setDevices(data, env, status) {
    data.devices.filter(d => env.deviceIds.includes(d.id)).forEach(d => { d.status = status })
  }
  function scopeFor(data, record) {
    return { envIds: environmentIdsFor(record), deviceIds: deviceIdsFor(record, data) }
  }
  function scopeLabel(data, record) {
    return environmentIdsFor(record).map(id => data.environments.find(e => e.id === id)?.name || id).join('、')
  }
  function leaseFor(data, id) {
    const lease = data.leases.find(l => l.id === id)
    if (!lease || lease.user !== me(data).name) throw new Error('只能操作自己的借用记录。')
    return lease
  }
  function me(data) {
    return data.users.find(u => u.id === data.currentUserId) || data.users.find(u => u.role === 'admin' && u.status === 'active') || data.users[0]
  }
  function requireAdmin(data, what) {
    const user = me(data)
    if (user.role !== 'admin') throw new Error(`${what}仅管理员可执行，当前身份「${user.name}」为普通人员。`)
    return user
  }
  function findUser(data, id) {
    const user = data.users.find(u => u.id === id)
    if (!user) throw new Error('人员不存在，请刷新后重试。')
    return user
  }
  function activeAdmins(data) {
    return data.users.filter(u => u.role === 'admin' && u.status === 'active')
  }
  const nextId = (prefix, rows) => `${prefix}-${Math.max(2000, ...rows.map(r => Number(r.id.split('-').at(-1)) || 0)) + 1}`
  return {
    async load() { await wait(latency); return { snapshot: read(), persistenceWarning } },
    reset() { return mutate(data => { requireAdmin(data, '重置演示数据'); Object.assign(data, createSeed()); activity(data, '已恢复初始演示数据', 'green') }) },
    borrow({ envIds, envId, hours, purpose }) {
      return mutate(data => {
        if (![2, 4, 8, 24, 48].includes(Number(hours))) throw new Error('请选择有效的借用时长。')
        if (typeof purpose !== 'string' || !purpose.trim() || purpose.trim().length > 200) throw new Error('请填写 1–200 字的借用用途。')
        const requested = envIds === undefined ? (envId ? [envId] : []) : envIds
        if (!Array.isArray(requested) || !requested.length || requested.some(id => typeof id !== 'string' || !id.trim())) throw new Error('请至少选择一个有效环境。')
        const requestedEnvIds = [...new Set(requested)]
        // Evaluate every environment independently against the same current resource snapshot.
        const outcomes = requestedEnvIds.map(envId => {
          const env = data.environments.find(item => item.id === envId)
          const failure = borrowFailure(data, env)
          return { envId, name: env?.name || envId, status: failure ? 'skipped' : 'borrowed', ...(failure || { code: 'available', reason: '空闲环境已直接借用，无需审批' }) }
        })
        const selected = outcomes.filter(item => item.status === 'borrowed').map(item => getEnv(data, item.envId))
        if (!selected.length) return { lease: null, requestedEnvIds, outcomes }
        const deviceIds = [...new Set(selected.flatMap(env => env.deviceIds))]
        const started = now()
        const lease = { id: nextId('LEASE', data.leases), envIds: selected.map(env => env.id), deviceIds, requestOutcomes: outcomes, user: me(data).name, purpose: purpose.trim(), hours: Number(hours), status: 'active', start: started.toISOString(), end: new Date(started.getTime() + Number(hours) * 3600000).toISOString() }
        data.leases.unshift(lease)
        setDevices(data, lease, 'leased')
        activity(data, `${me(data).name} 借用了 ${scopeLabel(data, lease)}（${selected.length} 个环境）`, 'green')
        return { lease, requestedEnvIds, outcomes }
      }, { shouldCommit: result => !!result.lease })
    },
    saveEnvironment(payload) {
      return mutate(data => { const env = writeEnvironment(data, payload, now().toISOString()); activity(data, `${me(data).name} ${payload.id ? '更新' : '新增'}了环境 ${env.name}`, 'green'); return env })
    },
    saveDevice(payload) {
      return mutate(data => { const device = writeDevice(data, payload, now().toISOString()); activity(data, `${me(data).name} ${payload.id ? '更新' : '新增'}了设备 ${device.name}`, 'green'); return device })
    },
    archiveEnvironment(id) {
      return mutate(data => { requireAdmin(data, '移出环境'); const env = archiveEnvironment(data, id, now().toISOString()); activity(data, `${me(data).name} 将 ${env.name} 移出了资源池`, 'amber'); return env })
    },
    retireDevice(id) {
      return mutate(data => { requireAdmin(data, '移出设备'); const device = retireDevice(data, id, now().toISOString()); activity(data, `${me(data).name} 将设备 ${device.name} 移出了资源池`, 'amber'); return device })
    },
    renew(id, hours = 4) {
      return mutate(data => {
        const lease = leaseFor(data, id)
        if (lease.status !== 'active') throw new Error('当前借用状态不可续期。')
        if (![2, 4, 8, 24, 48].includes(hours)) throw new Error('续期时长无效。')
        lease.end = new Date(Math.max(new Date(lease.end).getTime(), now().getTime()) + hours * 3600000).toISOString()
        activity(data, `${lease.id} 已续期 ${hours} 小时`)
        return lease
      })
    },
    requestReturn(id) {
      return mutate(data => {
        const lease = leaseFor(data, id)
        if (lease.status !== 'active') throw new Error('此借用已进入归还流程。')
        const scope = scopeFor(data, lease)
        Object.assign(lease, scope)
        lease.status = 'checking'
        setDevices(data, scope, 'checking')
        activity(data, `${lease.id} 已提交归还，等待健康复核`, 'amber')
        return lease
      })
    },
    verifyReturn(id, healthy) {
      return mutate(data => {
        const lease = leaseFor(data, id)
        if (lease.status !== 'checking') throw new Error('请先提交归还，完成后再进行健康复核。')
        const scope = scopeFor(data, lease)
        lease.status = healthy ? 'released' : 'isolated'
        setDevices(data, scope, healthy ? 'available' : 'isolated')
        if (!healthy) data.maintenance.unshift({ id: nextId('MT', data.maintenance), ...scope, reason: `${lease.id} 归还健康检查异常，整单设备已隔离`, status: 'isolated', user: me(data).name, date: now().toISOString(), issue: '待关联' })
        activity(data, `${scopeLabel(data, lease)} 健康复核${healthy ? '通过，已释放' : '异常，已隔离'}`, healthy ? 'green' : 'red')
      })
    },
    startMaintenance({ envId, reason }) {
      return mutate(data => {
        if (!reason?.trim() || reason.length > 200) throw new Error('请填写 1–200 字的维护原因。')
        const env = getEnv(data, envId)
        available(data, env)
        setDevices(data, env, 'maintenance')
        data.maintenance.unshift({ id: nextId('MT', data.maintenance), envId, reason: reason.trim(), status: 'maintenance', user: me(data).name, date: now().toISOString(), issue: '待关联' })
        activity(data, `${env.name} 进入维护状态`, 'amber')
      })
    },
    verifyMaintenance(id, healthy) {
      return mutate(data => {
        const record = data.maintenance.find(m => m.id === id)
        if (!record || !['maintenance', 'isolated'].includes(record.status)) throw new Error('维护记录已结束。')
        const scope = scopeFor(data, record)
        const occupied = scope.deviceIds.some(deviceId => data.devices.some(d => d.id === deviceId && !['available', 'maintenance', 'isolated'].includes(d.status)))
        if (occupied) throw new Error('仍有共享设备占用，无法完成健康复核。')
        setDevices(data, scope, healthy ? 'available' : 'isolated')
        record.status = healthy ? 'success' : 'isolated'
        activity(data, `${scopeLabel(data, record)} 维护复核${healthy ? '通过，恢复可用' : '异常，保持隔离'}`, healthy ? 'green' : 'red')
      })
    },
    startRun({ pipelineId, envId }) {
      return mutate(data => {
        const pipeline = data.pipelines.find(p => p.id === pipelineId)
        if (!pipeline || pipeline.status !== 'published') throw new Error('只有已发布配置可以启动任务。')
        const env = getEnv(data, envId)
        if (pipeline.arch !== env.arch) throw new Error('环境架构与流水线要求不匹配。')
        available(data, env)
        const run = { id: nextId('RUN', data.runs), envId, name: pipeline.name, status: 'running', result: null, progress: 12, passed: 0, total: 48, duration: 0, build: '#mock', version: 'OS 24.03 RC2', user: me(data).name, started: now().toISOString(), artifact: 'os-24.03-rc2.img', config: `${pipeline.id}@${pipeline.version}`, evidence: 1 }
        data.runs.unshift(run)
        setDevices(data, env, 'running')
        activity(data, `${run.id} 已启动 ${pipeline.name}`)
        return run
      })
    },
    finishRun(id, success = true) {
      return mutate(data => {
        const run = data.runs.find(r => r.id === id)
        if (!run || !['running', 'reconciling'].includes(run.status)) throw new Error('任务当前状态不可结束。')
        run.status = 'cleaning'
        run.result = success ? 'success' : 'failed'
        run.progress = 100
        run.passed = success ? run.total : run.total - 1
        run.evidence = 4
        setDevices(data, getEnv(data, run.envId), 'cleaning')
        activity(data, `${id} 已确认终态，等待清理`, 'amber')
      })
    },
    cleanupRun(id, healthy) {
      return mutate(data => {
        const run = data.runs.find(r => r.id === id)
        if (!run || run.status !== 'cleaning') throw new Error('必须确认任务终态后再清理释放。')
        run.status = run.result || 'failed'
        run.evidence = 5
        setDevices(data, getEnv(data, run.envId), healthy ? 'available' : 'isolated')
        if (!healthy) data.maintenance.unshift({ id: nextId('MT', data.maintenance), envId: run.envId, reason: `${id} 清理异常，设备已隔离`, status: 'isolated', user: me(data).name, date: now().toISOString(), issue: '待关联' })
        activity(data, `${id} 清理${healthy ? '通过，设备已释放' : '异常，设备已隔离'}`, healthy ? 'green' : 'red')
      })
    },
    addUser({ name, role = 'member' }) {
      return mutate(data => {
        const actor = requireAdmin(data, '新增人员')
        const trimmed = typeof name === 'string' ? name.trim() : ''
        if (!trimmed || trimmed.length > 20) throw new Error('请填写 1–20 字的人员姓名。')
        if (!['admin', 'member'].includes(role)) throw new Error('请选择有效的角色。')
        if (data.users.some(u => u.name === trimmed)) throw new Error('同名人员已存在。')
        const user = { id: nextId('U', data.users), name: trimmed, role, status: 'active', title: role === 'admin' ? '系统测试组 · 管理员' : '系统测试组 · 成员', joined: now().toISOString().slice(0, 10) }
        data.users.push(user)
        activity(data, `${actor.name} 新增了${role === 'admin' ? '管理员' : '人员'} ${trimmed}`, 'green')
        return user
      })
    },
    setUserRole(id, role) {
      return mutate(data => {
        const actor = requireAdmin(data, '调整角色')
        const user = findUser(data, id)
        if (user.id === actor.id) throw new Error('不能调整当前身份自己的角色。')
        if (!['admin', 'member'].includes(role)) throw new Error('请选择有效的角色。')
        if (user.role === 'admin' && role === 'member' && activeAdmins(data).length === 1) throw new Error('至少保留一名启用的管理员。')
        user.role = role
        activity(data, `${actor.name} 将 ${user.name} 设为${role === 'admin' ? '管理员' : '普通人员'}`, role === 'admin' ? 'green' : 'blue')
        return user
      })
    },
    setUserStatus(id, status) {
      return mutate(data => {
        const actor = requireAdmin(data, '停用或启用人员')
        const user = findUser(data, id)
        if (user.id === actor.id) throw new Error('不能停用或启用当前身份自己。')
        if (!['active', 'disabled'].includes(status)) throw new Error('状态无效。')
        if (user.status === status) throw new Error(status === 'disabled' ? '该人员已停用。' : '该人员已启用。')
        if (user.role === 'admin' && status === 'disabled' && activeAdmins(data).length === 1) throw new Error('至少保留一名启用的管理员。')
        user.status = status
        activity(data, `${actor.name} 已${status === 'disabled' ? '停用' : '启用'} ${user.name}`, status === 'disabled' ? 'amber' : 'green')
        return user
      })
    },
    switchUser(id) {
      return mutate(data => {
        const user = findUser(data, id)
        if (user.status !== 'active') throw new Error('该人员已停用，不能切换为此身份。')
        if (user.id === data.currentUserId) throw new Error('当前已是此身份。')
        data.currentUserId = user.id
        activity(data, `演示身份已切换为 ${user.name}（${user.role === 'admin' ? '管理员' : '普通人员'}）`, 'blue')
        return user
      })
    },
  }
}
