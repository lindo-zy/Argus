import test from 'node:test'
import assert from 'node:assert/strict'
import { createMockApi } from '../src/mock/api.js'
import { DATA_KEY, enrichEnvironments, overview, environmentIdsFor } from '../src/mock/seed.js'

function storage() {
  const values = new Map()
  return { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) }
}
const setup = () => createMockApi({ storage: storage(), latency: 0, now: () => new Date('2026-10-30T07:00:00Z') })
const env = (snapshot, id) => enrichEnvironments(snapshot).find(e => e.id === id)

test('shared devices lock every referencing environment and unlock only after maintenance verification', async () => {
  const api = setup()
  const { snapshot } = await api.load()
  assert.equal(env(snapshot, 'ENV-ST-04').status, 'maintenance')
  assert.equal(env(snapshot, 'ENV-NET-06').status, 'maintenance')
  const unavailable = await api.borrow({ envId: 'ENV-NET-06', hours: 4, purpose: '共享设备验证' })
  assert.equal(unavailable.result.lease, null)
  assert.match(unavailable.result.outcomes[0].reason, /共享设备/)
  const verified = await api.verifyMaintenance('MT-003', true)
  assert.equal(env(verified.snapshot, 'ENV-NET-06').status, 'available')
  const borrowed = await api.borrow({ envId: 'ENV-NET-06', hours: 4, purpose: '共享设备验证' })
  assert.equal(env(borrowed.snapshot, 'ENV-ST-04').status, 'leased')
  await assert.rejects(api.startMaintenance({ envId: 'ENV-ST-04', reason: '不能覆盖借用' }), /共享设备/)
})

test('concurrent borrow requests recheck occupancy and only one succeeds', async () => {
  const api = setup()
  const results = await Promise.allSettled([
    api.borrow({ envId: 'ENV-ARM-01', hours: 4, purpose: '请求 A' }),
    api.borrow({ envId: 'ENV-ARM-01', hours: 8, purpose: '请求 B' }),
  ])
  assert.equal(results.filter(r => r.status === 'fulfilled' && r.value.result.lease).length, 1)
  const { snapshot } = await api.load()
  assert.equal(snapshot.leases.filter(l => environmentIdsFor(l).includes('ENV-ARM-01')).length, 1)
})

test('return requires health verification; failed verification isolates and blocks reuse', async () => {
  const api = setup()
  const borrowed = await api.borrow({ envId: 'ENV-ARM-01', hours: 4, purpose: '归还检查' })
  const id = borrowed.result.lease.id
  await assert.rejects(api.verifyReturn(id, true), /先提交归还/)
  const pending = await api.requestReturn(id)
  assert.equal(env(pending.snapshot, 'ENV-ARM-01').status, 'checking')
  assert.equal((await api.borrow({ envId: 'ENV-ARM-01', hours: 4, purpose: '不能提前复用' })).result.lease, null)
  const failed = await api.verifyReturn(id, false)
  assert.equal(env(failed.snapshot, 'ENV-ARM-01').status, 'isolated')
  const record = failed.snapshot.maintenance.find(m => environmentIdsFor(m).includes('ENV-ARM-01'))
  const fixed = await api.verifyMaintenance(record.id, true)
  assert.equal(env(fixed.snapshot, 'ENV-ARM-01').status, 'available')
})

test('healthy return frees resources, renew extends end time, and duplicate return is rejected', async () => {
  const api = setup()
  const borrowed = await api.borrow({ envId: 'ENV-VM-03', hours: 4, purpose: '借用闭环' })
  const renewed = await api.renew(borrowed.result.lease.id)
  assert.equal(new Date(renewed.result.end) - new Date(borrowed.result.lease.end), 4 * 3600000)
  await api.requestReturn(borrowed.result.lease.id)
  await assert.rejects(api.requestReturn(borrowed.result.lease.id), /已进入归还/)
  const result = await api.verifyReturn(borrowed.result.lease.id, true)
  assert.equal(env(result.snapshot, 'ENV-VM-03').status, 'available')
})

test('reconciling and active executions must finish then clear before releasing devices', async () => {
  const api = setup()
  await assert.rejects(api.cleanupRun('RUN-1025', true), /确认任务终态/)
  const finished = await api.finishRun('RUN-1025', true)
  const target = finished.snapshot.runs.find(r => r.id === 'RUN-1025').envId
  assert.equal(env(finished.snapshot, target).status, 'cleaning')
  assert.equal(finished.snapshot.runs.find(r => r.id === 'RUN-1025').evidence, 4)
  assert.equal((await api.borrow({ envId: target, hours: 4, purpose: '不能抢占' })).result.lease, null)
  const cleared = await api.cleanupRun('RUN-1025', true)
  assert.equal(env(cleared.snapshot, target).status, 'available')
  assert.equal(cleared.snapshot.runs.find(r => r.id === 'RUN-1025').evidence, 5)
  await assert.rejects(api.cleanupRun('RUN-1025', true), /确认任务终态/)
})

test('pipeline rejects drafts and mismatched architectures; cleanup failure isolates devices', async () => {
  const api = setup()
  await assert.rejects(api.startRun({ pipelineId: 'storage-chaos', envId: 'ENV-ARM-01' }), /已发布/)
  await assert.rejects(api.startRun({ pipelineId: 'os-smoke-test', envId: 'ENV-ARM-01' }), /架构/)
  const run = await api.startRun({ pipelineId: 'os-smoke-test', envId: 'ENV-VM-03' })
  assert.equal(env(run.snapshot, 'ENV-VM-03').status, 'running')
  await api.finishRun(run.result.id, false)
  const result = await api.cleanupRun(run.result.id, false)
  assert.equal(env(result.snapshot, 'ENV-VM-03').status, 'isolated')
  assert.ok(result.snapshot.maintenance.find(m => m.envId === 'ENV-VM-03'))
})

test('pipeline configs expose read-only contract XML and legacy snapshots are migrated in place', async () => {
  const { snapshot } = await setup().load()
  assert.ok(snapshot.pipelines.some(p => p.status === 'published') && snapshot.pipelines.some(p => p.status === 'draft'))
  for (const pipeline of snapshot.pipelines) {
    assert.match(pipeline.xml, new RegExp(`^<pipeline id="${pipeline.id}" name="${pipeline.name}" version="\\d+">`))
    assert.match(pipeline.xml, new RegExp(`<jenkins connection="jenkins-main" job="${pipeline.job}" />`))
    assert.match(pipeline.xml, new RegExp(`<item name="architecture" value="${pipeline.arch}" />`))
    assert.match(pipeline.xml, /<occupancy mode="exclusive" \/>/)
  }
  // 旧版本本地数据缺 xml 字段：按 seed 补齐，用户借用等其余数据保持不变。
  const local = storage()
  const first = createMockApi({ storage: local, latency: 0 })
  await first.borrow({ envId: 'ENV-ARM-01', hours: 4, purpose: '迁移前置数据' })
  const stored = JSON.parse(local.getItem(DATA_KEY))
  stored.pipelines = stored.pipelines.map(({ xml, ...pipeline }) => pipeline)
  local.setItem(DATA_KEY, JSON.stringify(stored))
  const migrated = await createMockApi({ storage: local, latency: 0 }).load()
  assert.ok(migrated.snapshot.pipelines.every(p => p.xml?.includes('<jenkins')))
  assert.ok(migrated.snapshot.leases.some(l => l.purpose === '迁移前置数据'))
})

test('invalid inputs do not mutate the snapshot and snapshots persist across service instances', async () => {
  const local = storage()
  const api = createMockApi({ storage: local, latency: 0 })
  await assert.rejects(api.borrow({ envId: 'ENV-ARM-01', hours: -1, purpose: '无效时长' }))
  await assert.rejects(api.borrow({ envId: 'ENV-ARM-01', hours: 4, purpose: '  ' }))
  assert.equal((await api.load()).snapshot.revision, 0)
  await api.borrow({ envId: 'ENV-ARM-01', hours: 4, purpose: '持久化验证' })
  const second = createMockApi({ storage: local, latency: 0 })
  assert.equal(env((await second.load()).snapshot, 'ENV-ARM-01').status, 'leased')
  const reset = await second.reset()
  assert.equal(env(reset.snapshot, 'ENV-ARM-01').status, 'available')
})

test('blocked or corrupted browser storage falls back to usable in-memory data', async () => {
  const local = storage()
  local.setItem(DATA_KEY, '{invalid')
  const corrupt = await createMockApi({ storage: local, latency: 0 }).load()
  assert.equal(corrupt.persistenceWarning, true)
  assert.equal(corrupt.snapshot.environments.length, 24)
  const denied = createMockApi({ latency: 0, storage: { getItem() { throw new Error('denied') }, setItem() { throw new Error('denied') } } })
  await denied.borrow({ envId: 'ENV-VM-03', hours: 4, purpose: '内存回退' })
  assert.equal(env((await denied.load()).snapshot, 'ENV-VM-03').status, 'leased')
})

test('historical trend totals, rates and failure breakdown agree across version and date filters', () => {
  for (const version of ['OS 24.03 RC2', 'OS 24.03 RC1']) for (const days of [3, 7]) {
    const stats = overview(version, days)
    assert.equal(stats.points.length, days)
    assert.equal(stats.failures.reduce((sum, f) => sum + f.count, 0), stats.total - stats.passed)
    assert.equal(stats.rate, (stats.passed / stats.total * 100).toFixed(1))
  }
  assert.equal(overview().total, 1248)
  assert.equal(overview().runs, 128)
  assert.equal(overview().passed, 1208)
})
