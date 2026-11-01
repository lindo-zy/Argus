import test from 'node:test'
import assert from 'node:assert/strict'
import { createMockApi } from '../src/mock/api.js'
import { CURRENT_USER, DATA_KEY, enrichEnvironments } from '../src/mock/seed.js'

function setup() {
  const values = new Map()
  const storage = { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) }
  return { api: createMockApi({ storage, latency: 0 }), storage }
}
const deviceInput = { name: '用户新增设备', arch: 'ARM64', type: 'physical', cpu: '32 核', memory: '64 GB', location: '实验室 B', ip: '10.24.99.1' }
const envInput = deviceIds => ({ name: '用户联合环境', arch: 'ARM64', type: 'physical', description: '用户自己维护', version: 'OS 24.03 RC2', location: '实验室 B', deviceIds })
const borrow = envIds => ({ envIds, hours: 4, purpose: '自助资源验证' })

test('a user can create a device and environment, then borrow immediately without approval', async () => {
  const { api } = setup()
  const device = (await api.saveDevice(deviceInput)).result
  const created = await api.saveEnvironment(envInput([device.id]))
  assert.equal(created.result.owner, CURRENT_USER)
  assert.equal(enrichEnvironments(created.snapshot).find(e => e.id === created.result.id).status, 'available')
  const result = await api.borrow(borrow([created.result.id]))
  assert.deepEqual(result.result.lease.envIds, [created.result.id])
  assert.equal(result.result.lease.status, 'active')
  assert.equal(result.snapshot.devices.find(d => d.id === device.id).status, 'leased')
  assert.equal(result.snapshot.borrowRequests, undefined)
})

test('self-service editing updates metadata and associations without changing previous lease snapshots', async () => {
  const { api } = setup()
  const first = (await api.saveDevice(deviceInput)).result
  const second = (await api.saveDevice({ ...deviceInput, name: '第二台设备' })).result
  const created = (await api.saveEnvironment(envInput([first.id]))).result
  const borrowed = (await api.borrow(borrow([created.id]))).result.lease
  await api.requestReturn(borrowed.id)
  await api.verifyReturn(borrowed.id, true)
  await api.saveDevice({ ...first, name: '重新命名设备' })
  const updated = await api.saveEnvironment({ ...created, name: '更新后的资源池环境', deviceIds: [second.id] })
  assert.deepEqual(updated.result.deviceIds, [second.id])
  assert.deepEqual(updated.snapshot.leases.find(l => l.id === borrowed.id).deviceIds, [first.id])
})

test('busy devices and their environments cannot be edited, archived or forced idle through payload fields', async () => {
  const { api } = setup()
  const before = (await api.load()).snapshot
  const busyEnv = before.environments.find(e => e.id === 'ENV-X86-02')
  const busyDevice = before.devices.find(d => d.id === busyEnv.deviceIds[0])
  await assert.rejects(api.saveDevice({ ...busyDevice, status: 'available', name: '不能覆盖' }), /正在使用/)
  await assert.rejects(api.saveEnvironment({ ...busyEnv, deviceIds: before.environments[0].deviceIds }), /释放后/)
  await assert.rejects(api.archiveEnvironment(busyEnv.id), /释放后/)
  await assert.rejects(api.retireDevice(busyDevice.id), /占用或维护/)
  assert.deepEqual((await api.load()).snapshot, before)
})

test('metadata edits cannot change isolated health into available', async () => {
  const { api } = setup()
  const isolated = (await api.load()).snapshot.devices.find(d => d.status === 'isolated')
  const result = await api.saveDevice({ ...isolated, name: '保留隔离状态', status: 'available' })
  assert.equal(result.result.status, 'isolated')
})

test('archiving removes resources from the active pool but preserves history and prevents stale selections blocking others', async () => {
  const { api } = setup()
  const device = (await api.saveDevice(deviceInput)).result
  const environment = (await api.saveEnvironment(envInput([device.id]))).result
  await assert.rejects(api.retireDevice(device.id), /仍被环境引用/)
  const archived = await api.archiveEnvironment(environment.id)
  assert.ok(!enrichEnvironments(archived.snapshot).some(e => e.id === environment.id))
  assert.ok(archived.snapshot.environments.some(e => e.id === environment.id))
  const result = await api.borrow(borrow([environment.id, 'ENV-ARM-01']))
  assert.deepEqual(result.result.lease.envIds, ['ENV-ARM-01'])
  assert.equal(result.result.outcomes[0].code, 'retired')
  const retired = await api.retireDevice(device.id)
  assert.equal(retired.result.lifecycle, 'retired')
  assert.equal(retired.snapshot.devices.find(d => d.id === device.id).status, 'available')
  assert.ok(retired.snapshot.activities.some(a => a.text.includes('移出')))
})

test('configuration validation rejects empty, missing, retired, mismatched and occupied device associations', async () => {
  const { api } = setup()
  const before = (await api.load()).snapshot
  await assert.rejects(api.saveEnvironment(envInput([])), /至少关联/)
  await assert.rejects(api.saveEnvironment(envInput(['MISSING'])), /不存在/)
  const x86 = before.devices.find(d => d.arch === 'x86_64')
  await assert.rejects(api.saveEnvironment(envInput([x86.id])), /架构、类型/)
  const occupiedArm = before.devices.find(d => d.arch === 'ARM64' && d.status === 'leased')
  await assert.rejects(api.saveEnvironment(envInput([occupiedArm.id])), /正在使用/)
  const referenced = before.devices.find(d => d.id === 'DEV-001')
  await assert.rejects(api.saveDevice({ ...referenced, arch: 'x86_64' }), /仍被环境引用/)
  assert.deepEqual((await api.load()).snapshot, before)
})

test('user resource changes persist and IDs are not reused when a resource leaves the pool', async () => {
  const { api, storage } = setup()
  const device = (await api.saveDevice(deviceInput)).result
  await api.retireDevice(device.id)
  const next = (await api.saveDevice(deviceInput)).result
  assert.notEqual(device.id, next.id)
  const fresh = createMockApi({ storage, latency: 0 })
  assert.equal((await fresh.load()).snapshot.devices.find(d => d.id === next.id).name, deviceInput.name)
})

test('a malformed environment or removed device is skipped without blocking a valid environment', async () => {
  const { api, storage } = setup()
  const snapshot = (await api.load()).snapshot
  snapshot.environments.find(e => e.id === 'ENV-VM-03').deviceIds = ['MISSING']
  storage.setItem(DATA_KEY, JSON.stringify(snapshot))
  const result = await api.borrow(borrow(['ENV-VM-03', 'ENV-ARM-01']))
  assert.deepEqual(result.result.lease.envIds, ['ENV-ARM-01'])
  assert.equal(result.result.outcomes[0].code, 'invalid_configuration')
})
