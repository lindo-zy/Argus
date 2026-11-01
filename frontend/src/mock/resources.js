import { CURRENT_USER, environmentStatus } from './seed.js'

const active = item => item.lifecycle !== 'retired'
const occupied = status => ['leased', 'running', 'checking', 'cleaning', 'reconciling', 'maintenance'].includes(status)
function text(value, label, max, required = false) {
  if (value !== undefined && typeof value !== 'string') throw new Error(`${label}格式无效。`)
  const result = (value || '').trim()
  if ((required && !result) || result.length > max) throw new Error(`${label}${required ? '不能为空，且' : ''}不能超过 ${max} 字。`)
  return result
}
function platform(arch, type) {
  if (!['ARM64', 'x86_64'].includes(arch)) throw new Error('请选择有效的处理器架构。')
  if (!['physical', 'virtual'].includes(type)) throw new Error('请选择物理机或虚拟机类型。')
}
function getDevice(data, id) {
  const device = data.devices.find(d => d.id === id && active(d))
  if (!device) throw new Error(`设备 ${id} 不存在或已移出资源池。`)
  return device
}
function getEnvironment(data, id) {
  const env = data.environments.find(e => e.id === id && active(e))
  if (!env) throw new Error('环境不存在或已移出资源池。')
  return env
}
function assertEditable(data, env) {
  if (env.deviceIds.some(id => occupied(data.devices.find(d => d.id === id)?.status))) throw new Error('环境设备正在使用、维护或等待复核，释放后才能调整资源配置。')
}
const nextId = (prefix, rows) => `${prefix}-${Math.max(2000, ...rows.map(r => Number(r.id.split('-').at(-1)) || 0)) + 1}`

export function writeEnvironment(data, payload, timestamp) {
  const previous = payload.id ? getEnvironment(data, payload.id) : null
  if (previous) assertEditable(data, previous)
  const name = text(payload.name, '环境名称', 60, true)
  const description = text(payload.description, '环境用途', 200)
  const version = text(payload.version, '系统版本', 80)
  const location = text(payload.location, '部署位置', 100)
  platform(payload.arch, payload.type)
  if (!Array.isArray(payload.deviceIds) || !payload.deviceIds.length || payload.deviceIds.some(id => typeof id !== 'string')) throw new Error('请至少关联一台有效设备。')
  const deviceIds = [...new Set(payload.deviceIds)]
  const devices = deviceIds.map(id => getDevice(data, id))
  if (devices.some(d => d.arch !== payload.arch || d.type !== payload.type)) throw new Error('关联设备的架构、类型必须与环境要求一致。')
  if (devices.some(d => occupied(d.status))) throw new Error('关联设备正在使用或维护，请选择其他设备。')
  const env = { ...previous, id: previous?.id || nextId('ENV', data.environments), name, description, version, location,
    arch: payload.arch, type: payload.type, deviceIds, owner: previous?.owner || CURRENT_USER,
    lifecycle: 'active', tags: [payload.arch, payload.type === 'physical' ? '物理机' : '虚拟机'],
    health: previous && previous.arch === payload.arch && previous.type === payload.type && previous.deviceIds.length === deviceIds.length && previous.deviceIds.every(id => deviceIds.includes(id)) ? previous.health : '待检查', updatedAt: timestamp, createdAt: previous?.createdAt || timestamp,
  }
  if (previous) Object.assign(previous, env)
  else data.environments.unshift(env)
  return env
}

export function writeDevice(data, payload, timestamp) {
  const previous = payload.id ? getDevice(data, payload.id) : null
  if (previous && occupied(previous.status)) throw new Error('设备正在使用、维护或等待复核，释放后才能编辑。')
  const name = text(payload.name, '设备名称', 60, true)
  const cpu = text(payload.cpu, 'CPU 规格', 100)
  const memory = text(payload.memory, '内存规格', 60)
  const location = text(payload.location, '设备位置', 100)
  const ip = text(payload.ip, '设备地址', 100)
  platform(payload.arch, payload.type)
  const host = payload.type === 'virtual' ? text(payload.host, '宿主机', 100) : ''
  if (previous && data.environments.some(e => active(e) && e.deviceIds.includes(previous.id) && (e.arch !== payload.arch || e.type !== payload.type))) throw new Error('设备仍被环境引用，请先调整关联后再修改架构或类型。')
  const device = { ...previous, id: previous?.id || nextId('DEV', data.devices), name, cpu, memory, location, ip, host,
    arch: payload.arch, type: payload.type, status: previous?.status || 'available', lifecycle: 'active',
    createdBy: previous?.createdBy || CURRENT_USER, updatedAt: timestamp, createdAt: previous?.createdAt || timestamp,
  }
  if (previous) Object.assign(previous, device)
  else data.devices.unshift(device)
  return device
}

export function archiveEnvironment(data, id, timestamp) {
  const env = getEnvironment(data, id)
  assertEditable(data, env)
  env.lifecycle = 'retired'
  env.updatedAt = timestamp
  return env
}

export function retireDevice(data, id, timestamp) {
  const device = getDevice(data, id)
  if (occupied(device.status)) throw new Error('设备仍有占用或维护，释放后才能移出资源池。')
  if (data.environments.some(e => active(e) && e.deviceIds.includes(id))) throw new Error('设备仍被环境引用，请先编辑关联环境或将环境移出资源池。')
  device.lifecycle = 'retired'
  device.updatedAt = timestamp
  return device
}

export function borrowFailure(data, env) {
  if (!env) return { code: 'not_found', reason: '环境不存在或已被移除' }
  if (!active(env)) return { code: 'retired', reason: '环境已移出资源池' }
  if (!env.deviceIds.length) return { code: 'invalid_configuration', reason: '环境尚未关联设备' }
  if (env.deviceIds.some(id => !data.devices.some(d => d.id === id && active(d)))) return { code: 'invalid_configuration', reason: '关联设备不存在或已移出资源池' }
  const status = environmentStatus(env, data.devices)
  const reasons = { leased: '环境或共享设备已被借用', running: '环境或共享设备正在执行任务', maintenance: '环境或共享设备正在维护', isolated: '关联设备已隔离，等待健康复核', reconciling: '关联设备有待对账任务', cleaning: '关联设备正在等待清理', checking: '关联设备正在等待归还复核' }
  return status === 'available' ? null : { code: status, reason: reasons[status] || '环境当前不可借用' }
}
