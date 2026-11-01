// 拓扑页面与 Mock 共用的纯数据规则；真实保存仍由服务端重新校验。
export const activeResources = rows => rows.filter(row => row.lifecycle !== 'retired')
export const sameIds = (a, b) => a.length === b.length && a.every(id => b.includes(id))
export const idleDevice = device => !!device && device.lifecycle !== 'retired' && device.status === 'available'
export const memberships = (envs, deviceId) => activeResources(envs).filter(env => env.deviceIds.includes(deviceId))
export const editableEnvironment = (env, devices) => env?.lifecycle !== 'retired' && env?.deviceIds?.length > 0 && env.deviceIds.every(id => idleDevice(devices.find(d => d.id === id)))

export function topologyPlan(snapshot, { deviceIds, targetId, sourceId, mode }) {
  if (!deviceIds.length) throw new Error('请先选择需要调整的设备。')
  const envs = activeResources(snapshot.environments)
  const target = envs.find(e => e.id === targetId)
  if (!target) throw new Error('请选择目标环境。')
  if (!editableEnvironment(target, snapshot.devices)) throw new Error('目标环境正在使用、维护或等待复核，暂不可调整。')
  if (mode === 'move' && targetId === sourceId) throw new Error('目标环境不能与当前环境相同。')
  const selected = [...new Set(deviceIds)].map(id => snapshot.devices.find(d => d.id === id))
  if (selected.some(d => !idleDevice(d))) throw new Error('所选设备正在使用、维护、隔离或已移出，暂不可调整。')
  if (selected.some(d => d.arch !== target.arch || d.type !== target.type)) throw new Error('设备的架构、类型必须与目标环境一致。')
  const changes = []
  if (mode === 'move') {
    const source = envs.find(e => e.id === sourceId)
    if (!source || !deviceIds.every(id => source.deviceIds.includes(id))) throw new Error('请选择所有已选设备共同关联的当前环境。')
    if (!editableEnvironment(source, snapshot.devices)) throw new Error('当前环境正在使用、维护或等待复核，暂不可调整。')
    const remaining = source.deviceIds.filter(id => !deviceIds.includes(id))
    if (!remaining.length) throw new Error('原环境至少需要保留一台设备。')
    changes.push({ envId: source.id, expectedDeviceIds: [...source.deviceIds], deviceIds: remaining })
  } else if (mode !== 'add') throw new Error('请选择有效的操作方式。')
  const next = [...new Set([...target.deviceIds, ...deviceIds])]
  if (!sameIds(next, target.deviceIds)) changes.push({ envId: target.id, expectedDeviceIds: [...target.deviceIds], deviceIds: next })
  if (!changes.length) throw new Error('所选设备已关联目标环境，无需重复添加。')
  return { changes }
}

export function applyTopology(data, payload, timestamp) {
  const changes = payload?.changes
  if (!Array.isArray(changes) || !changes.length || changes.length > 100) throw new Error('请提交 1–100 个环境的关联变化。')
  const ids = new Set()
  const prepared = []
  for (const change of changes) {
    if (!change || typeof change.envId !== 'string' || !change.envId) throw new Error('环境关联参数无效。')
    if (ids.has(change.envId)) throw new Error('同一环境只能提交一次关联变化。')
    ids.add(change.envId)
    for (const key of ['expectedDeviceIds', 'deviceIds']) {
      const list = change[key]
      if (!Array.isArray(list) || !list.length || list.some(id => typeof id !== 'string' || !id.trim())) throw new Error('环境至少需要关联一台有效设备，并提供原关联快照。')
      if (new Set(list).size !== list.length) throw new Error('设备关联不能重复。')
    }
    const env = data.environments.find(e => e.id === change.envId && e.lifecycle !== 'retired')
    if (!env) throw new Error('环境不存在或已移出资源池。')
    if (!sameIds(env.deviceIds, change.expectedDeviceIds)) {
      const error = new Error('环境拓扑已变化，请刷新数据后重新调整。')
      error.code = 'topology_conflict'; throw error
    }
    if (sameIds(env.deviceIds, change.deviceIds)) continue
    if (!editableEnvironment(env, data.devices)) throw new Error('环境设备正在使用、维护或等待复核，释放后才能调整资源配置。')
    const target = change.deviceIds.map(id => data.devices.find(d => d.id === id && d.lifecycle !== 'retired'))
    if (target.some(d => !d)) throw new Error('关联设备不存在或已移出资源池。')
    if (target.some(d => d.arch !== env.arch || d.type !== env.type)) throw new Error('关联设备的架构、类型必须与环境要求一致。')
    if (target.some(d => !idleDevice(d))) throw new Error('关联设备正在使用或维护，请选择其他设备。')
    prepared.push({ env, change })
  }
  for (const { env, change } of prepared) {
    for (const id of new Set([...env.deviceIds, ...change.deviceIds])) {
      const device = data.devices.find(d => d.id === id)
      device.health = 'unknown'
    }
    env.deviceIds = [...change.deviceIds]
    env.topologyVersion = (env.topologyVersion || 1) + 1
    env.health = '待检查'; env.updatedAt = timestamp
  }
  // 共享设备健康结论变化也应反映在所有引用它的环境中。
  for (const env of activeResources(data.environments)) {
    if (env.deviceIds.some(id => data.devices.find(d => d.id === id)?.health === 'unknown')) env.health = '待检查'
  }
  return { environmentIds: prepared.map(p => p.env.id), changed: prepared.length }
}
