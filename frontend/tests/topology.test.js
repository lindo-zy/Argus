import test from 'node:test'
import assert from 'node:assert/strict'
import { createMockApi } from '../src/mock/api.js'
import { createSeed, DATA_KEY } from '../src/mock/seed.js'
import { topologyPlan, memberships, applyTopology, editableEnvironment } from '../src/topology.js'
import { createHttpApi } from '../src/api/createHttpApi.js'
function fixture() {
  const data = createSeed()
  data.environments.push({ id: 'ENV-TARGET', name: '目标环境', arch: 'ARM64', type: 'physical', deviceIds: ['DEV-001'] })
  const storage = new Map([[DATA_KEY, JSON.stringify(data)]])
  return { data, api: createMockApi({ latency: 0, storage: { getItem: k => storage.get(k), setItem: (k, v) => storage.set(k, v) } }) }
}
const choice = { deviceIds: ['DEV-002'], sourceId: 'ENV-ARM-01', targetId: 'ENV-TARGET', mode: 'move' }
test('migration atomically changes memberships, invalidates health, preserves history and rejects replay', async () => {
  const { api, data } = fixture(), payload = topologyPlan(data, choice)
  assert.equal(payload.changes.length, 2)
  const saved = await api.saveTopology(payload)
  assert.equal(saved.result.changed, 2)
  assert.deepEqual(memberships(saved.snapshot.environments, 'DEV-002').map(e => e.id), ['ENV-TARGET'])
  assert.equal(saved.snapshot.environments.find(e => e.id === 'ENV-TARGET').health, '待检查')
  assert.deepEqual(saved.snapshot.leases, data.leases)
  await assert.rejects(api.saveTopology(payload), /拓扑已变化/)
  assert.deepEqual((await api.load()).snapshot, saved.snapshot)
})
test('shared association preserves original membership and unassigned devices can be attached', () => {
  const { data } = fixture()
  applyTopology(data, topologyPlan(data, { ...choice, mode: 'add' }), 'now')
  assert.equal(memberships(data.environments, 'DEV-002').length, 2)
  data.devices.push({ id: 'NEW', arch: 'ARM64', type: 'physical', status: 'available' })
  applyTopology(data, topologyPlan(data, { ...choice, deviceIds: ['NEW'], mode: 'add' }), 'now')
  assert.equal(memberships(data.environments, 'NEW')[0].id, 'ENV-TARGET')
})
test('target validation failure leaves source and revision intact', async () => {
  const { api, data } = fixture(), payload = topologyPlan(data, choice)
  payload.changes[1].deviceIds.push('DEV-005')
  await assert.rejects(api.saveTopology(payload), /架构、类型/)
  assert.deepEqual((await api.load()).snapshot, data)
})
test('every locked device state is rejected, and retired source cannot be edited', () => {
  for (const status of ['leased', 'running', 'maintenance', 'isolated', 'checking', 'cleaning', 'reconciling']) {
    const { data } = fixture(); data.devices.find(d => d.id === 'DEV-002').status = status
    assert.throws(() => topologyPlan(data, choice), /正在使用|暂不可调整/)
  }
  const { data } = fixture(); data.environments[0].lifecycle = 'retired'
  assert.throws(() => topologyPlan(data, choice), /当前环境/)
})
test('last-device removal, incompatible target, duplicate environment and stale preview rejected', () => {
  const { data } = fixture()
  assert.throws(() => topologyPlan(data, { ...choice, sourceId: 'ENV-TARGET', targetId: 'ENV-ARM-01', deviceIds: ['DEV-001'] }), /至少需要保留/)
  assert.throws(() => topologyPlan(data, { ...choice, targetId: 'ENV-VM-03' }), /架构、类型/)
  const plan = topologyPlan(data, choice)
  assert.throws(() => applyTopology(data, { changes: [...plan.changes, plan.changes[0]] }, 'now'), /同一环境/)
  plan.changes[1].expectedDeviceIds = ['DEV-003']
  assert.throws(() => applyTopology(data, plan, 'now'), /拓扑已变化/)
  assert.equal(data.environments[0].deviceIds.includes('DEV-002'), true)
})
test('busy sibling locks whole environment; shared device stays a unique asset', () => {
  const { data } = fixture()
  assert.equal(memberships(data.environments, 'DEV-014').length, 2)
  data.devices.find(d => d.id === 'DEV-004').status = 'leased'
  assert.equal(editableEnvironment(data.environments[0], data.devices), false)
  assert.throws(() => topologyPlan(data, choice), /当前环境/)
})
test('borrow after preview blocks commit; disabled actors cannot edit', async () => {
  const { data, api } = fixture(), plan = topologyPlan(data, choice)
  await api.borrow({ envIds: ['ENV-ARM-01'], hours: 4, purpose: '竞争验证' })
  await assert.rejects(api.saveTopology(plan), /释放后/)
  const stored = (await api.load()).snapshot
  assert.equal(stored.environments[0].deviceIds.includes('DEV-002'), true)
  stored.users[0].status = 'disabled'
  const mock = createMockApi({ latency: 0, storage: { getItem: () => JSON.stringify(stored) } })
  await assert.rejects(mock.saveTopology(plan), /停用/)
})
test('HTTP sends one atomic payload with actor and preserves conflict details', async () => {
  const { data } = fixture(), payload = topologyPlan(data, choice), calls = []
  const api = createHttpApi({ storage: null, fetch: async (url, options) => {
    calls.push({ url, options }); return { ok: true, json: async () => ({ result: { changed: 2 }, snapshot: data }) }
  } })
  const response = await api.saveTopology(payload)
  assert.equal(calls.length, 1); assert.ok(calls[0].url.endsWith('/api/topology'))
  assert.equal(calls[0].options.headers['X-Argus-User'], 'U-1001')
  assert.deepEqual(JSON.parse(calls[0].options.body), payload); assert.equal(response.result.changed, 2)
  const denied = createHttpApi({ storage: null, fetch: async () => ({ ok: false, status: 400, json: async () => ({ detail: '拓扑已变化', code: 'topology_conflict' }) }) })
  await assert.rejects(denied.saveTopology(payload), e => e.code === 'topology_conflict')
})
