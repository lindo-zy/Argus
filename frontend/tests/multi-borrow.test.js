import test from 'node:test'
import assert from 'node:assert/strict'
import { createMockApi } from '../src/mock/api.js'
import { DATA_KEY, enrichEnvironments, environmentIdsFor } from '../src/mock/seed.js'

const env = (snapshot, id) => enrichEnvironments(snapshot).find(e => e.id === id)
function setup() {
  const values = new Map()
  const storage = { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) }
  return { storage, api: createMockApi({ storage, latency: 0 }) }
}
const request = envIds => ({ envIds, hours: 8, purpose: '多环境联合验证' })

test('one multi-environment application creates one persisted lease linked from every selected environment', async () => {
  const { api, storage } = setup()
  const before = (await api.load()).snapshot
  const result = await api.borrow(request(['ENV-ARM-01', 'ENV-VM-03']))
  assert.equal(result.snapshot.leases.length, before.leases.length + 1)
  assert.deepEqual(result.result.lease.envIds, ['ENV-ARM-01', 'ENV-VM-03'])
  assert.equal(result.result.lease.deviceIds.length, 7)
  for (const id of result.result.lease.envIds) {
    assert.equal(env(result.snapshot, id).status, 'leased')
    assert.equal(env(result.snapshot, id).lease.id, result.result.lease.id)
  }
  const second = createMockApi({ storage, latency: 0 })
  const restored = (await second.load()).snapshot.leases.find(l => l.id === result.result.lease.id)
  assert.deepEqual(restored, result.result.lease)
  const renewed = await second.renew(restored.id)
  assert.equal(new Date(renewed.result.end) - new Date(restored.end), 4 * 3600000)
})

test('unavailable or missing environments are skipped while the available environments are borrowed', async () => {
  const { api } = setup()
  const before = (await api.load()).snapshot
  const result = await api.borrow(request(['ENV-ARM-01', 'ENV-X86-02', 'MISSING', 'ENV-VM-03']))
  assert.deepEqual(result.result.lease.envIds, ['ENV-ARM-01', 'ENV-VM-03'])
  assert.deepEqual(result.result.outcomes.map(item => item.status), ['borrowed','skipped','skipped','borrowed'])
  assert.equal(result.result.outcomes[1].code, 'running')
  assert.equal(result.result.outcomes[2].code, 'not_found')
  assert.equal(env(result.snapshot, 'ENV-X86-02').status, 'running')
  assert.equal(result.snapshot.leases.length, before.leases.length + 1)
  assert.deepEqual(result.result.lease.requestOutcomes, result.result.outcomes)
})

test('an entirely unavailable batch reports reasons without creating a lease or modifying resources', async () => {
  const { api } = setup()
  const before = (await api.load()).snapshot
  const result = await api.borrow(request(['ENV-X86-02', 'ENV-ST-04', 'MISSING']))
  assert.equal(result.result.lease, null)
  assert.ok(result.result.outcomes.every(item => item.status === 'skipped' && item.reason))
  assert.deepEqual(result.snapshot, before)
  assert.deepEqual((await api.load()).snapshot, before)
})

test('invalid request structure still rejects without changing data', async () => {
  const { api } = setup()
  const before = (await api.load()).snapshot
  for (const invalid of [[], '', null, [null], [''], [42]]) {
    await assert.rejects(api.borrow(request(invalid)), /至少选择/)
    assert.deepEqual((await api.load()).snapshot, before)
  }
})

test('shared devices and duplicate environment IDs are deduplicated inside a single lease', async () => {
  const { api } = setup()
  await api.verifyMaintenance('MT-003', true)
  const borrowed = await api.borrow(request(['ENV-ST-04', 'ENV-NET-06', 'ENV-ST-04']))
  assert.deepEqual(borrowed.result.lease.envIds, ['ENV-ST-04', 'ENV-NET-06'])
  assert.equal(borrowed.result.lease.deviceIds.length, 6)
  assert.equal(new Set(borrowed.result.lease.deviceIds).size, 6)
  await api.requestReturn(borrowed.result.lease.id)
  const checked = (await api.load()).snapshot
  assert.ok(borrowed.result.lease.envIds.every(id => env(checked, id).status === 'checking'))
  assert.equal((await api.borrow(request(['ENV-NET-06']))).result.lease, null)
  const released = await api.verifyReturn(borrowed.result.lease.id, true)
  assert.ok(borrowed.result.lease.envIds.every(id => env(released.snapshot, id).status === 'available'))
})

test('failed batch return creates one maintenance scope and recovery verifies all devices in the batch', async () => {
  const { api } = setup()
  const borrowed = await api.borrow(request(['ENV-ARM-01', 'ENV-VM-03']))
  await api.requestReturn(borrowed.result.lease.id)
  const isolated = await api.verifyReturn(borrowed.result.lease.id, false)
  const record = isolated.snapshot.maintenance.find(m => environmentIdsFor(m).includes('ENV-ARM-01'))
  assert.deepEqual(record.envIds, borrowed.result.lease.envIds)
  assert.deepEqual(record.deviceIds, borrowed.result.lease.deviceIds)
  assert.ok(record.envIds.every(id => env(isolated.snapshot, id).status === 'isolated'))
  const recovered = await api.verifyMaintenance(record.id, true)
  assert.ok(record.envIds.every(id => env(recovered.snapshot, id).status === 'available'))
})

test('overlapping concurrent batches still acquire their free resources without duplicate device claims', async () => {
  const { api } = setup()
  const before = (await api.load()).snapshot
  const results = await Promise.all([
    api.borrow(request(['ENV-ARM-01','ENV-VM-03'])),
    api.borrow(request(['ENV-VM-03','ENV-007'])),
  ])
  const newLeases = results.map(response => response.result.lease)
  assert.ok(newLeases.every(Boolean))
  const deviceIds = newLeases.flatMap(lease => lease.deviceIds)
  assert.equal(new Set(deviceIds).size, deviceIds.length)
  assert.equal(results.flatMap(response => response.result.outcomes).filter(item => item.envId === 'ENV-VM-03' && item.status === 'borrowed').length, 1)
  const after = (await api.load()).snapshot
  assert.equal(after.leases.length, before.leases.length + 2)
  for (const id of ['ENV-ARM-01','ENV-VM-03','ENV-007']) assert.equal(env(after,id).status,'leased')
})

test('legacy single-environment leases can still be renewed, returned and verified', async () => {
  const { api } = setup()
  const legacy = (await api.load()).snapshot.leases.find(l => l.envId === 'ENV-ARM-05')
  assert.equal(legacy.envIds, undefined)
  await api.renew(legacy.id)
  const pending = await api.requestReturn(legacy.id)
  assert.deepEqual(pending.result.envIds, ['ENV-ARM-05'])
  const released = await api.verifyReturn(legacy.id, true)
  assert.equal(env(released.snapshot, 'ENV-ARM-05').status, 'available')
})

test('return uses the acquired device snapshot instead of an edited environment membership', async () => {
  const { api, storage } = setup()
  const borrowed = await api.borrow(request(['ENV-ARM-01', 'ENV-VM-03']))
  const changed = JSON.parse(storage.getItem(DATA_KEY))
  const environment = changed.environments.find(e => e.id === 'ENV-ARM-01')
  const unrelated = changed.devices.find(d => d.status === 'running')
  environment.deviceIds = [unrelated.id]
  storage.setItem(DATA_KEY, JSON.stringify(changed))
  await api.requestReturn(borrowed.result.lease.id)
  const result = await api.verifyReturn(borrowed.result.lease.id, true)
  assert.ok(borrowed.result.lease.deviceIds.every(id => result.snapshot.devices.find(d => d.id === id).status === 'available'))
  assert.equal(result.snapshot.devices.find(d => d.id === unrelated.id).status, 'running')
})
