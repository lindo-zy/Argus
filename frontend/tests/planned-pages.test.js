import test from 'node:test'
import assert from 'node:assert/strict'
import { createUpgradeStore, UPGRADE_STAGES } from '../src/mock/upgrade.js'
import { createPerfStore, getPerfStore, evaluateMetric, roundMetricValue, generatePerfResults, perfMetricSummary, perfVersionStats, PERF_DIRECTIONS } from '../src/mock/perf.js'

// 三个规划页（升级流水线 / 性能流水线 / 性能数据看板）的 Mock 数据层约束。
// 与 mock.test.js 相同的约定：注入内存 storage 与固定时钟，latency=0 便于断言。

function storage() {
  const values = new Map()
  return {
    getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    corrupt() { for (const key of ['argus.upgrade.v1', 'argus.perf.v1']) values.set(key, '{broken') },
  }
}
const admin = { name: '王磊', role: 'admin' }
const member = { name: '张明', role: 'member' }
const upgradeSetup = (extra = {}) => createUpgradeStore({ storage: storage(), latency: 0, now: () => new Date('2026-11-03T15:30:00+08:00'), ...extra })
const perfSetup = (extra = {}) => createPerfStore({ storage: storage(), latency: 0, now: () => new Date('2026-11-03T15:30:00+08:00'), ...extra })
const upgradePayload = over => ({ name: '校验用配置', description: '', arch: 'ARM64', baseVersion: 'OS 24.03', targetVersion: 'OS 24.03-R5', packageRef: 'jfrog://argus/x.zip', packageSha256: 'a'.repeat(64), ...over })

// ---- 升级流水线演示 store ----

test('upgrade seed provides published pipelines, draft and historical runs with fixed stages', async () => {
  const { snapshot } = await upgradeSetup().load()
  assert.equal(snapshot.pipelines.length, 3)
  assert.equal(snapshot.pipelines.filter(p => p.status === 'published').length, 2)
  assert.equal(snapshot.runs.length, 4)
  assert.equal(snapshot.runs[0].id, 'UPL-3001')
  const failed = snapshot.runs.find(run => run.status === 'failed')
  assert.equal(failed.stages.length, UPGRADE_STAGES.length)
  assert.equal(failed.versionAfter, '')
  assert.ok(failed.stages.find(stage => stage.status === 'failed' && stage.error))
})

test('upgrade draft save validates fields and members may create drafts', async () => {
  const api = upgradeSetup()
  const created = await api.saveUpgradePipeline({ ...upgradePayload({ name: '补丁升级' }), actor: member })
  assert.ok(created.result.id)
  const { snapshot } = await api.load()
  const pipeline = snapshot.pipelines.find(p => p.id === created.result.id)
  assert.equal(pipeline.status, 'draft')
  await assert.rejects(api.saveUpgradePipeline({ ...upgradePayload({ id: created.result.id, name: '  ' }), actor: member }), /名称/)
  await assert.rejects(api.saveUpgradePipeline({ ...upgradePayload({ id: created.result.id, packageSha256: 'xyz' }), actor: member }), /SHA-256/)
  await assert.rejects(api.saveUpgradePipeline({ ...upgradePayload({ id: created.result.id, targetVersion: 'OS 24.03' }), actor: member }), /相同/)
  await assert.rejects(api.saveUpgradePipeline({ ...upgradePayload({ id: created.result.id, unknown: 1 }), actor: member }), /未知参数/)
})

test('upgrade publish is admin-only, immutable once published, and copy creates a fresh draft', async () => {
  const api = upgradeSetup()
  const draft = api.saveUpgradePipeline({ name: '待发布', arch: 'x86_64', baseVersion: 'OS 24.03', targetVersion: 'OS 24.05', packageRef: 'jfrog://argus/y.zip', packageSha256: 'b'.repeat(64), actor: member })
  const id = (await draft).result.id
  await assert.rejects(api.publishUpgradePipeline(id, member), /仅管理员/)
  await api.publishUpgradePipeline(id, admin)
  const { snapshot } = await api.load()
  assert.equal(snapshot.pipelines.find(p => p.id === id).status, 'published')
  await assert.rejects(api.publishUpgradePipeline(id, admin), /已发布/)
  await assert.rejects(api.saveUpgradePipeline({ id, name: '改名', arch: 'x86_64', baseVersion: 'OS 24.03', targetVersion: 'OS 24.05', packageRef: 'jfrog://argus/y.zip', packageSha256: 'b'.repeat(64), actor: admin }), /不可修改/)
  const copy = await api.copyUpgradePipeline(id, member)
  const after = await api.load()
  const copied = after.snapshot.pipelines.find(p => p.id === copy.result.id)
  assert.equal(copied.status, 'draft')
  assert.match(copied.name, /副本/)
})

test('upgrade runs require published config and matching arch, one running per pipeline', async () => {
  const api = upgradeSetup()
  await assert.rejects(api.startUpgradeRun({ pipelineId: 'UP-2003', envId: 'ENV-VM-03', actor: member }), /已发布/)
  await assert.rejects(api.startUpgradeRun({ pipelineId: 'UP-2001', envId: 'ENV-ARM-01', envArch: 'ARM64', actor: member }), /架构/)
  const started = await api.startUpgradeRun({ pipelineId: 'UP-2001', envId: 'ENV-VM-03', envName: '虚拟化冒烟环境', envArch: 'x86_64', actor: member })
  const runId = started.result.id
  assert.match(runId, /^UPL-\d+$/)
  await assert.rejects(api.startUpgradeRun({ pipelineId: 'UP-2001', envId: 'ENV-X86-02', actor: member }), /已有运行中/)
  await assert.rejects(api.startUpgradeRun({ pipelineId: 'UP-2001', envId: '', actor: member }), /目标环境/)
  const { snapshot } = await api.load()
  const run = snapshot.runs.find(item => item.id === runId)
  assert.equal(run.status, 'running')
  assert.equal(run.stages.filter(stage => stage.status === 'pending').length, UPGRADE_STAGES.length - 1)
  assert.equal(run.stages[0].status, 'running')
  assert.match(run.started, /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/)
})

test('upgrade run advances stage by stage and records the upgraded version', async () => {
  const api = upgradeSetup()
  const { result } = await api.startUpgradeRun({ pipelineId: 'UP-2001', envId: 'ENV-VM-03', envName: '虚拟化冒烟环境', envArch: 'x86_64', actor: admin })
  let status = 'running'
  for (let i = 0; i < UPGRADE_STAGES.length; i++) status = (await api.tickUpgradeRun(result.id)).result.status
  assert.equal(status, 'success')
  await assert.rejects(api.tickUpgradeRun(result.id), /已结束/)
  const { snapshot } = await api.load()
  const run = snapshot.runs.find(item => item.id === result.id)
  assert.equal(run.versionAfter, run.targetVersion)
  assert.ok(run.stages.every(stage => stage.status === 'success' && stage.duration > 0))
  assert.match(run.ended, /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/)
})

test('upgrade manual failure marks current stage and stops the run', async () => {
  const api = upgradeSetup()
  const { result } = await api.startUpgradeRun({ pipelineId: 'UP-2002', envId: 'ENV-ARM-01', envName: 'ARM64 兼容性环境', envArch: 'ARM64', actor: admin })
  await api.tickUpgradeRun(result.id)
  await api.failUpgradeRun(result.id, { reason: '断电' })
  const { snapshot } = await api.load()
  const run = snapshot.runs.find(item => item.id === result.id)
  assert.equal(run.status, 'failed')
  assert.equal(run.error, '断电')
  const failedStage = run.stages.find(stage => stage.status === 'failed')
  assert.ok(failedStage)
  assert.equal(run.stages.filter(stage => stage.status === 'success').length, 1)
})

test('upgrade demo data persists across store instances and survives corrupt storage with a warning', async () => {
  const shared = storage()
  const first = createUpgradeStore({ storage: shared, latency: 0, now: () => new Date() })
  await first.saveUpgradePipeline({ name: '持久化验证', arch: 'ARM64', baseVersion: 'OS 24.02', targetVersion: 'OS 24.03', packageRef: 'jfrog://argus/z.zip', packageSha256: 'c'.repeat(64), actor: admin })
  const second = createUpgradeStore({ storage: shared, latency: 0, now: () => new Date() })
  const { snapshot } = await second.load()
  assert.ok(snapshot.pipelines.some(p => p.name === '持久化验证'))
  shared.corrupt()
  const recovered = await createUpgradeStore({ storage: shared, latency: 0, now: () => new Date() }).load()
  assert.equal(recovered.persistenceWarning, true)
  assert.equal(recovered.snapshot.pipelines.length, 3) // 回退到初始样本
})

// ---- 性能流水线演示 store ----

test('perf seed carries published pipelines with metric baselines and historical results', async () => {
  const { snapshot } = await perfSetup().load()
  assert.equal(snapshot.pipelines.length, 3)
  const boot = snapshot.pipelines.find(p => p.id === 'PF-2001')
  assert.equal(boot.status, 'published')
  assert.ok(boot.metrics.every(m => m.baseline > 0 && m.threshold > 0 && PERF_DIRECTIONS[m.direction]))
  const failedRun = snapshot.runs.find(run => run.status === 'failed')
  assert.equal(failedRun.results.length, 0)
  assert.ok(snapshot.runs.some(run => run.results.some(result => result.verdict === 'regress')))
})

test('evaluateMetric judges regression, pass and improvement per direction', () => {
  // 越低越好：超出允许波动（坏方向）回归；好方向超出 1% 优化；其余达标。
  const lower = { direction: 'lower', baseline: 1000, threshold: 5 }
  assert.deepEqual(evaluateMetric(lower, 1060), { deltaPct: 6, verdict: 'regress' })
  assert.deepEqual(evaluateMetric(lower, 1030), { deltaPct: 3, verdict: 'pass' })
  assert.deepEqual(evaluateMetric(lower, 980), { deltaPct: -2, verdict: 'improve' })
  assert.deepEqual(evaluateMetric(lower, 995), { deltaPct: -0.5, verdict: 'pass' })
  const higher = { direction: 'higher', baseline: 100, threshold: 8 }
  assert.equal(evaluateMetric(higher, 91).verdict, 'regress')
  assert.equal(evaluateMetric(higher, 109).verdict, 'improve')
  assert.equal(evaluateMetric(higher, 105).verdict, 'improve') // 好方向超出 1% 即优化
  assert.equal(evaluateMetric(higher, 100.5).verdict, 'pass')
  assert.throws(() => evaluateMetric({ direction: 'lower', baseline: 0, threshold: 5 }, 1), /基线/)
})

test('roundMetricValue keeps readable precision by baseline magnitude', () => {
  assert.equal(roundMetricValue(18500.4, 18500), 18500)
  assert.equal(roundMetricValue(23.46, 30), 23.5)
  assert.equal(roundMetricValue(9.842, 9.8), 9.84)
  assert.equal(roundMetricValue(1.842, 1.8), 1.84)
})

test('perf draft save validates metric definitions and members may create drafts', async () => {
  const api = perfSetup()
  const metrics = [{ key: 'boot-time', name: '开机耗时', unit: 'ms', direction: 'lower', baseline: 18000, threshold: 5 }]
  const created = await api.savePerfPipeline({ name: '新性能配置', description: '', arch: 'x86_64', metrics, actor: member })
  assert.ok(created.result.id)
  const { snapshot } = await api.load()
  const pipeline = snapshot.pipelines.find(p => p.id === created.result.id)
  assert.equal(pipeline.metrics[0].baseline, 18000)
  await assert.rejects(api.savePerfPipeline({ name: '坏键名', arch: 'x86_64', metrics: [{ ...metrics[0], key: 'Boot Time' }], actor: member }), /指标标识/)
  await assert.rejects(api.savePerfPipeline({ name: '重复键', arch: 'x86_64', metrics: [metrics[0], { ...metrics[0], name: '第二项' }], actor: member }), /重复/)
  await assert.rejects(api.savePerfPipeline({ name: '坏基线', arch: 'x86_64', metrics: [{ ...metrics[0], baseline: 0 }], actor: member }), /基线/)
  await assert.rejects(api.savePerfPipeline({ name: '坏阈值', arch: 'x86_64', metrics: [{ ...metrics[0], threshold: 101 }], actor: member }), /允许波动/)
  await assert.rejects(api.savePerfPipeline({ name: '无指标', arch: 'x86_64', metrics: [], actor: member }), /1–8/)
  await assert.rejects(api.savePerfPipeline({ name: '坏方向', arch: 'x86_64', metrics: [{ ...metrics[0], direction: 'down' }], actor: member }), /方向/)
})

test('perf publish is admin-only and published configs are immutable', async () => {
  const api = perfSetup()
  const metrics = [{ key: 'io', name: '吞吐', unit: 'MB/s', direction: 'higher', baseline: 1000, threshold: 8 }]
  const { result } = await api.savePerfPipeline({ name: '待发布性能', arch: 'ARM64', metrics, actor: member })
  await assert.rejects(api.publishPerfPipeline(result.id, member), /仅管理员/)
  await api.publishPerfPipeline(result.id, admin)
  await assert.rejects(api.publishPerfPipeline(result.id, admin), /已发布/)
  await assert.rejects(api.savePerfPipeline({ id: result.id, name: '改', arch: 'ARM64', metrics, actor: admin }), /不可修改/)
})

test('perf collection runs complete with deterministic metric results and verdicts', async () => {
  const api = perfSetup()
  await assert.rejects(api.startPerfRun({ pipelineId: 'PF-2003', envId: 'ENV-X86-02', version: 'OS 24.03', actor: member }), /已发布/)
  await assert.rejects(api.startPerfRun({ pipelineId: 'PF-2001', envId: 'ENV-ARM-01', envArch: 'ARM64', version: 'OS 24.03', actor: member }), /架构/)
  await assert.rejects(api.startPerfRun({ pipelineId: 'PF-2001', envId: 'ENV-X86-02', version: '  ', actor: member }), /被测版本/)
  const { result } = await api.startPerfRun({ pipelineId: 'PF-2001', envId: 'ENV-X86-02', envName: 'x86 稳定性环境', envArch: 'x86_64', version: 'OS 24.05-rc1', actor: member })
  const runId = result.id
  await assert.rejects(api.startPerfRun({ pipelineId: 'PF-2001', envId: 'ENV-VM-03', version: 'OS 24.03', actor: member }), /已有运行中/)
  let status = 'running'
  while (status === 'running') status = (await api.tickPerfRun(runId)).result.status
  assert.equal(status, 'success')
  const { snapshot } = await api.load()
  const run = snapshot.runs.find(item => item.id === runId)
  assert.equal(run.progress, 100)
  const keys = run.results.map(item => item.key)
  assert.deepEqual(keys.sort(), ['boot-time', 'cpu-idle', 'memory-footprint'])
  // 噪声区间 -8% ~ +12%，且判定与偏差方向自洽。
  for (const item of run.results) {
    assert.ok(item.deltaPct >= -8 && item.deltaPct <= 12, `${item.key} 偏差越界: ${item.deltaPct}`)
    const verdict = evaluateMetric(item, item.value).verdict
    assert.equal(verdict, item.verdict)
  }
})

test('perf result generation is deterministic per run id', async () => {
  const { snapshot } = await perfSetup().load()
  const pipeline = snapshot.pipelines.find(p => p.id === 'PF-2001')
  const first = generatePerfResults(pipeline, 'PR-DETERMINISTIC')
  const second = generatePerfResults(pipeline, 'PR-DETERMINISTIC')
  assert.deepEqual(first, second)
  const other = generatePerfResults(pipeline, 'PR-OTHER')
  assert.notDeepEqual(first.map(item => item.value), other.map(item => item.value))
})

test('perf collection can fail midway and running guard is released', async () => {
  const api = perfSetup()
  const { result } = await api.startPerfRun({ pipelineId: 'PF-2002', envId: 'ENV-ARM-05', envName: 'ARM64 性能基准环境', envArch: 'ARM64', version: 'OS 24.03', actor: admin })
  await api.failPerfRun(result.id, { reason: '代理失联' })
  const { snapshot } = await api.load()
  const run = snapshot.runs.find(item => item.id === result.id)
  assert.equal(run.status, 'failed')
  assert.equal(run.error, '代理失联')
  const again = await api.startPerfRun({ pipelineId: 'PF-2002', envId: 'ENV-ARM-05', envName: 'ARM64 性能基准环境', envArch: 'ARM64', version: 'OS 24.03', actor: admin })
  assert.ok(again.result.id)
})

test('perfMetricSummary aggregates latest verdicts and regression counts per metric', async () => {
  const { snapshot } = await perfSetup().load()
  const summaries = perfMetricSummary(snapshot)
  const byKey = Object.fromEntries(summaries.map(item => [item.key, item]))
  assert.deepEqual(new Set(summaries.map(item => item.key)), new Set(['boot-time', 'memory-footprint', 'cpu-idle', 'mem-peak', 'power-avg']))
  const boot = byKey['boot-time']
  assert.equal(boot.runCount, 5)
  assert.equal(boot.regressCount, 1)
  assert.equal(boot.latest.runId, 'PR-3001')
  assert.equal(boot.baseline, 18500)
  const power = byKey['power-avg']
  assert.equal(power.regressCount, 1)
  assert.equal(power.latest.runId, 'PR-3002')
  // 输出按最近一次采集倒序：mem-peak（10-30 10:12）最新。
  assert.equal(summaries[0].key, 'mem-peak')
})

test('perfVersionStats averages successful collections per version', async () => {
  const { snapshot } = await perfSetup().load()
  const boot = snapshot.pipelines.find(p => p.id === 'PF-2001').metrics.find(m => m.key === 'boot-time')
  const rows = perfVersionStats(snapshot, boot)
  const byVersion = Object.fromEntries(rows.map(row => [row.version, row]))
  assert.equal(byVersion['OS 24.04'].count, 2)
  assert.equal(byVersion['OS 24.04'].avg, 18850)
  assert.equal(byVersion['OS 24.04'].best, 17920) // 越低越好
  assert.equal(byVersion['OS 24.03'].count, 3)
  assert.equal(perfVersionStats(snapshot, null).length, 0)
})

test('perf store persists drafts across instances and recovers from corrupt storage', async () => {
  const shared = storage()
  const first = createPerfStore({ storage: shared, latency: 0, now: () => new Date() })
  await first.savePerfPipeline({ name: '看板持久化', arch: 'x86_64', metrics: [{ key: 'net-throughput', name: '网络吞吐', unit: 'MB/s', direction: 'higher', baseline: 940, threshold: 10 }], actor: admin })
  const second = createPerfStore({ storage: shared, latency: 0, now: () => new Date() })
  const { snapshot } = await second.load()
  assert.ok(snapshot.pipelines.some(p => p.name === '看板持久化'))
  shared.corrupt()
  const recovered = await createPerfStore({ storage: shared, latency: 0, now: () => new Date() }).load()
  assert.equal(recovered.persistenceWarning, true)
  assert.equal(recovered.snapshot.pipelines.length, 3)
})

test('getPerfStore returns a shared singleton for the pipeline page and dashboard', () => {
  assert.equal(getPerfStore(), getPerfStore())
})
