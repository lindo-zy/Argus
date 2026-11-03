// 性能流水线与性能数据看板共用演示数据层（规划页 · Mock 实现）。
// 流水线页负责配置指标与启动采集，看板页聚合同一份数据（argus.perf.v1）。
// 全部为本地演示：不占用真实设备、不连接采集代理，历史样本截至 2026-10-30。
import { createDemoStore, localStamp, defaultDemoStorage } from './demoStore.js'

export const PERF_DATA_KEY = 'argus.perf.v1'
export const PERF_DIRECTIONS = { lower: '越低越好', higher: '越高越好' }

const ARCHES = ['ARM64', 'x86_64']
const METRIC_KEY_RE = /^[a-z][a-z0-9-]{1,31}$/

// 按基线量级决定展示精度：≥100 取整，≥10 一位小数，其余两位。
export function roundMetricValue(value, baseline) {
  const digits = Math.abs(baseline) >= 100 ? 0 : Math.abs(baseline) >= 10 ? 1 : 2
  return Number(value.toFixed(digits))
}

// 纯判定函数：偏离基线超过允许阈值（坏方向）即回归，好方向超出 1% 记为优化，其余视为达标。
export function evaluateMetric({ direction, baseline, threshold }, value) {
  if (!Number.isFinite(baseline) || baseline <= 0) throw new Error('指标基线必须是正数。')
  const deltaPct = Math.round((value - baseline) / baseline * 1000) / 10
  const badDelta = direction === 'lower' ? deltaPct : -deltaPct
  const verdict = badDelta > threshold ? 'regress' : badDelta <= -1 ? 'improve' : 'pass'
  return { deltaPct, verdict }
}

// 由运行 ID 派生确定性伪随机序列：同一次运行重复计算得到相同指标值，测试与演示均可复现。
function seededRandom(text) {
  let h = 2166136261
  for (const ch of String(text)) { h ^= ch.charCodeAt(0); h = Math.imul(h, 16777619) }
  return () => {
    h = Math.imul(h ^ (h >>> 15), 2246822519)
    h = Math.imul(h ^ (h >>> 13), 3266489917)
    h ^= h >>> 16
    return (h >>> 0) / 4294967296
  }
}

function generateResults(pipeline, runId) {
  const random = seededRandom(`perf:${runId}`)
  return pipeline.metrics.map(metric => {
    // 噪声区间 -8% ~ +12%：回归偶有发生，符合性能采集演示的直观预期。
    const value = roundMetricValue(metric.baseline * (1 + (random() * 0.2 - 0.08)), metric.baseline)
    const { deltaPct, verdict } = evaluateMetric(metric, value)
    return { key: metric.key, name: metric.name, unit: metric.unit, direction: metric.direction, baseline: metric.baseline, threshold: metric.threshold, value, deltaPct, verdict }
  })
}

// 供测试与看板演示复算：同一 pipeline + runId 总是得到相同的指标结果。
export function generatePerfResults(pipeline, runId) {
  return generateResults(pipeline, runId)
}

// 固定历史样本截至 2026-10-30；runs 最新在前，与主快照 runs 的排序习惯一致。
function perfSeed() {
  const pipelines = [
    { id: 'PF-2001', name: '系统启动性能基线', description: '开机耗时、内存占用与稳态 CPU 采集，按版本对比基线', arch: 'x86_64', status: 'published',
      metrics: [
        { key: 'boot-time', name: '开机耗时', unit: 'ms', direction: 'lower', baseline: 18500, threshold: 5 },
        { key: 'memory-footprint', name: '启动后内存占用', unit: 'MB', direction: 'lower', baseline: 1420, threshold: 5 },
        { key: 'cpu-idle', name: '稳态空闲 CPU', unit: '%', direction: 'lower', baseline: 6.5, threshold: 20 },
      ], createdBy: '王磊', createdAt: '2026-10-24', updatedAt: '2026-10-28' },
    { id: 'PF-2002', name: 'ARM64 内存与功耗采集', description: '峰值内存与平均功耗监测，用于版本回归对比', arch: 'ARM64', status: 'published',
      metrics: [
        { key: 'mem-peak', name: '内存峰值', unit: 'MB', direction: 'lower', baseline: 2380, threshold: 6 },
        { key: 'power-avg', name: '平均功耗', unit: 'W', direction: 'lower', baseline: 9.8, threshold: 8 },
      ], createdBy: '张明', createdAt: '2026-10-24', updatedAt: '2026-10-27' },
    { id: 'PF-2003', name: 'IO 吞吐回归', description: '顺序写吞吐与 4K 随机写延迟（待补充基线数据来源）', arch: 'x86_64', status: 'draft',
      metrics: [
        { key: 'io-throughput', name: '顺序写吞吐', unit: 'MB/s', direction: 'higher', baseline: 1180, threshold: 8 },
        { key: 'fs-latency', name: '4K 随机写延迟', unit: 'ms', direction: 'lower', baseline: 1.8, threshold: 15 },
      ], createdBy: '李四', createdAt: '2026-10-30', updatedAt: '2026-10-30' },
  ]
  const metricOf = (pipelineId, key) => pipelines.find(p => p.id === pipelineId).metrics.find(m => m.key === key)
  const resultRow = (pipelineId, key, value) => {
    const metric = metricOf(pipelineId, key)
    const rounded = roundMetricValue(value, metric.baseline)
    const { deltaPct, verdict } = evaluateMetric(metric, rounded)
    return { key, name: metric.name, unit: metric.unit, direction: metric.direction, baseline: metric.baseline, threshold: metric.threshold, value: rounded, deltaPct, verdict }
  }
  const runs = [
    { id: 'PR-3001', pipelineId: 'PF-2001', pipelineName: pipelines[0].name, envId: 'ENV-X86-02', envName: 'x86 稳定性环境', arch: 'x86_64', version: 'OS 24.04', status: 'success', progress: 100,
      results: [resultRow('PF-2001', 'boot-time', 17920), resultRow('PF-2001', 'memory-footprint', 1396), resultRow('PF-2001', 'cpu-idle', 6.2)],
      user: '张明', started: '2026-10-30 08:30', ended: '2026-10-30 09:05', error: '' },
    { id: 'PR-3002', pipelineId: 'PF-2002', pipelineName: pipelines[1].name, envId: 'ENV-ARM-05', envName: 'ARM64 性能基准环境', arch: 'ARM64', version: 'OS 24.03-R2', status: 'success', progress: 100,
      results: [resultRow('PF-2002', 'mem-peak', 2465), resultRow('PF-2002', 'power-avg', 9.4)],
      user: '张明', started: '2026-10-30 10:12', ended: '2026-10-30 10:48', error: '' },
    { id: 'PR-3003', pipelineId: 'PF-2001', pipelineName: pipelines[0].name, envId: 'ENV-VM-03', envName: '虚拟化冒烟环境', arch: 'x86_64', version: 'OS 24.04', status: 'success', progress: 100,
      results: [resultRow('PF-2001', 'boot-time', 19780), resultRow('PF-2001', 'memory-footprint', 1448), resultRow('PF-2001', 'cpu-idle', 7.1)],
      user: '李四', started: '2026-10-29 15:02', ended: '2026-10-29 15:40', error: '' },
    { id: 'PR-3004', pipelineId: 'PF-2002', pipelineName: pipelines[1].name, envId: 'ENV-ARM-01', envName: 'ARM64 兼容性环境', arch: 'ARM64', version: 'OS 24.03-R2', status: 'success', progress: 100,
      results: [resultRow('PF-2002', 'mem-peak', 2410), resultRow('PF-2002', 'power-avg', 10.7)],
      user: '王磊', started: '2026-10-29 11:20', ended: '2026-10-29 11:55', error: '' },
    { id: 'PR-3005', pipelineId: 'PF-2001', pipelineName: pipelines[0].name, envId: 'ENV-X86-02', envName: 'x86 稳定性环境', arch: 'x86_64', version: 'OS 24.03', status: 'success', progress: 100,
      results: [resultRow('PF-2001', 'boot-time', 18410), resultRow('PF-2001', 'memory-footprint', 1415), resultRow('PF-2001', 'cpu-idle', 6.4)],
      user: '张明', started: '2026-10-28 09:00', ended: '2026-10-28 09:34', error: '' },
    { id: 'PR-3006', pipelineId: 'PF-2002', pipelineName: pipelines[1].name, envId: 'ENV-ARM-05', envName: 'ARM64 性能基准环境', arch: 'ARM64', version: 'OS 24.03', status: 'success', progress: 100,
      results: [resultRow('PF-2002', 'mem-peak', 2392), resultRow('PF-2002', 'power-avg', 9.9)],
      user: '李四', started: '2026-10-27 14:30', ended: '2026-10-27 15:02', error: '' },
    { id: 'PR-3007', pipelineId: 'PF-2001', pipelineName: pipelines[0].name, envId: 'ENV-VM-03', envName: '虚拟化冒烟环境', arch: 'x86_64', version: 'OS 24.03', status: 'success', progress: 100,
      results: [resultRow('PF-2001', 'boot-time', 18220), resultRow('PF-2001', 'memory-footprint', 1432), resultRow('PF-2001', 'cpu-idle', 6.8)],
      user: '王磊', started: '2026-10-26 10:05', ended: '2026-10-26 10:41', error: '' },
    { id: 'PR-3008', pipelineId: 'PF-2002', pipelineName: pipelines[1].name, envId: 'ENV-ARM-01', envName: 'ARM64 兼容性环境', arch: 'ARM64', version: 'OS 24.03', status: 'success', progress: 100,
      results: [resultRow('PF-2002', 'mem-peak', 2374), resultRow('PF-2002', 'power-avg', 9.6)],
      user: '张明', started: '2026-10-25 16:44', ended: '2026-10-25 17:18', error: '' },
    { id: 'PR-3009', pipelineId: 'PF-2001', pipelineName: pipelines[0].name, envId: 'ENV-X86-02', envName: 'x86 稳定性环境', arch: 'x86_64', version: 'OS 24.03', status: 'success', progress: 100,
      results: [resultRow('PF-2001', 'boot-time', 18100), resultRow('PF-2001', 'memory-footprint', 1408), resultRow('PF-2001', 'cpu-idle', 6.6)],
      user: '李四', started: '2026-10-24 09:15', ended: '2026-10-24 09:49', error: '' },
    { id: 'PR-3010', pipelineId: 'PF-2001', pipelineName: pipelines[0].name, envId: 'ENV-VM-03', envName: '虚拟化冒烟环境', arch: 'x86_64', version: 'OS 24.03', status: 'failed', progress: 42, results: [],
      user: '张明', started: '2026-10-30 13:20', ended: '2026-10-30 13:36', error: '采集代理心跳丢失，样本不足已丢弃' },
  ]
  return {
    schema: 1, revision: 0, pipelines, runs,
    activities: [
      { time: '13:36', text: 'PR-3010 采集失败：代理心跳丢失', tone: 'red' },
      { time: '10:48', text: 'PR-3002 采集完成，平均功耗低于基线', tone: 'green' },
      { time: '08:30', text: '张明 启动了系统启动性能基线采集', tone: 'blue' },
    ],
  }
}

function text(value, label, max, required = true) {
  if (value === undefined || value === null) value = ''
  if (typeof value !== 'string' || value !== value.trim()) throw new Error(`${label}格式无效。`)
  if (required && !value.trim()) throw new Error(`请填写${label}。`)
  if (value.trim().length > max) throw new Error(`${label}不能超过 ${max} 字。`)
  return value.trim()
}

function validateMetrics(raw) {
  if (!Array.isArray(raw) || raw.length < 1 || raw.length > 8) throw new Error('请为流水线定义 1–8 个采集指标。')
  const seen = new Set()
  return raw.map(item => {
    if (!item || typeof item !== 'object') throw new Error('指标定义格式无效。')
    if (Object.keys(item).some(key => !['key', 'name', 'unit', 'direction', 'baseline', 'threshold'].includes(key))) throw new Error('指标包含未知参数。')
    const key = text(item.key, '指标标识', 32)
    if (!METRIC_KEY_RE.test(key)) throw new Error(`指标标识 ${key} 需为小写字母开头的 2–32 位小写字母、数字或连字符。`)
    if (seen.has(key)) throw new Error(`指标标识 ${key} 重复。`)
    seen.add(key)
    const name = text(item.name, '指标名称', 40)
    const unit = text(item.unit, '指标单位', 20)
    if (!['lower', 'higher'].includes(item.direction)) throw new Error(`指标 ${name} 的方向必须是 lower 或 higher。`)
    const baseline = Number(item.baseline), threshold = Number(item.threshold)
    if (!Number.isFinite(baseline) || baseline <= 0) throw new Error(`指标 ${name} 的基线必须是正数。`)
    if (!Number.isFinite(threshold) || threshold <= 0 || threshold > 100) throw new Error(`指标 ${name} 的允许波动需在 0–100 之间。`)
    return { key, name, unit, direction: item.direction, baseline: roundMetricValue(baseline, baseline), threshold: Math.round(threshold * 10) / 10 }
  })
}

let shared = null
export function getPerfStore() {
  return shared ||= createPerfStore({ storage: defaultDemoStorage() })
}

export function createPerfStore({ storage, latency, now = () => new Date() } = {}) {
  const kernel = createDemoStore({ key: PERF_DATA_KEY, storage, latency, now, seed: perfSeed })
  const stamp = () => localStamp(now())

  return {
    load: kernel.load,
    savePerfPipeline(payload) {
      return kernel.mutate(data => {
        if (Object.keys(payload).some(key => !['id', 'name', 'description', 'arch', 'metrics', 'actor'].includes(key))) throw new Error('包含未知参数，性能配置只能声明受控字段。')
        const name = text(payload.name, '流水线名称', 60)
        const description = text(payload.description, '描述', 200, false)
        if (!ARCHES.includes(payload.arch)) throw new Error('请选择有效的处理器架构。')
        const metrics = validateMetrics(payload.metrics)
        let pipeline
        if (payload.id) {
          pipeline = data.pipelines.find(item => item.id === payload.id)
          if (!pipeline) throw new Error('性能流水线不存在，请刷新后重试。')
          if (pipeline.status !== 'draft') throw new Error('已发布配置不可修改，请复制为新草稿。')
        } else {
          pipeline = { id: kernel.nextId('PF', data.pipelines), status: 'draft', createdBy: payload.actor?.name || '演示用户', createdAt: now().toISOString().slice(0, 10) }
          data.pipelines.unshift(pipeline)
        }
        Object.assign(pipeline, { name, description, arch: payload.arch, metrics }, { updatedAt: now().toISOString().slice(0, 10) })
        kernel.activity(data, `${payload.actor?.name || '演示用户'} ${payload.id ? '更新了' : '新建了'}性能配置草稿 ${name}`, 'blue')
        return { id: pipeline.id }
      })
    },
    copyPerfPipeline(id, actor = {}) {
      return kernel.mutate(data => {
        const source = data.pipelines.find(item => item.id === id)
        if (!source) throw new Error('性能流水线不存在。')
        const copy = { ...source, metrics: source.metrics.map(m => ({ ...m })), id: kernel.nextId('PF', data.pipelines), name: (source.name + ' · 副本').slice(0, 60), status: 'draft', createdBy: actor.name || '演示用户', createdAt: now().toISOString().slice(0, 10), updatedAt: now().toISOString().slice(0, 10) }
        data.pipelines.unshift(copy)
        kernel.activity(data, `${actor.name || '演示用户'} 复制了性能配置 ${source.name}`, 'blue')
        return { id: copy.id }
      })
    },
    publishPerfPipeline(id, actor = {}) {
      return kernel.mutate(data => {
        kernel.requireAdmin(actor, '发布性能流水线')
        const pipeline = data.pipelines.find(item => item.id === id)
        if (!pipeline) throw new Error('性能流水线不存在。')
        if (pipeline.status === 'published') throw new Error('该配置已发布。')
        validateMetrics(pipeline.metrics)
        pipeline.status = 'published'
        pipeline.updatedAt = now().toISOString().slice(0, 10)
        kernel.activity(data, `${actor.name} 发布了性能配置 ${pipeline.name}`, 'green')
        return { id: pipeline.id }
      })
    },
    startPerfRun({ pipelineId, envId, envName, envArch, version, actor = {} } = {}) {
      return kernel.mutate(data => {
        const pipeline = data.pipelines.find(item => item.id === pipelineId)
        if (!pipeline || pipeline.status !== 'published') throw new Error('只有已发布的性能配置可以启动采集。')
        if (typeof envId !== 'string' || !envId.trim()) throw new Error('请选择采集环境。')
        if (envArch && envArch !== pipeline.arch) throw new Error('环境架构与性能配置不匹配。')
        const versionText = text(version, '被测版本', 40)
        if (data.runs.some(run => run.status === 'running' && run.pipelineId === pipelineId)) throw new Error('该配置已有运行中的采集，请等待完成后再启动。')
        const run = {
          id: kernel.nextId('PR', data.runs), pipelineId, pipelineName: pipeline.name,
          envId: envId.trim(), envName: envName || envId, arch: pipeline.arch, version: versionText,
          status: 'running', progress: 5, results: [], user: actor.name || '演示用户',
          started: stamp(), ended: '', error: '',
        }
        data.runs.unshift(run)
        kernel.activity(data, `${run.user} 启动了 ${pipeline.name} 采集（${run.envName} · ${versionText}）`, 'blue')
        return { id: run.id }
      })
    },
    // 推进采集进度；到达 100% 时按确定性噪声生成各指标结果（含回归判定）。
    tickPerfRun(id) {
      return kernel.mutate(data => {
        const run = data.runs.find(item => item.id === id)
        if (!run) throw new Error('采集运行不存在。')
        if (run.status !== 'running') throw new Error('该运行已结束。')
        const pipeline = data.pipelines.find(item => item.id === run.pipelineId)
        run.progress = Math.min(100, run.progress + 16)
        if (run.progress >= 100) {
          run.results = pipeline ? generateResults(pipeline, run.id) : []
          run.status = 'success'
          run.ended = stamp()
          const regress = run.results.filter(result => result.verdict === 'regress')
          kernel.activity(data, `${run.id} 采集完成${regress.length ? `，${regress.length} 项指标回归` : '，指标均达标'}`, regress.length ? 'amber' : 'green')
        }
        return { id: run.id, progress: run.progress, status: run.status }
      })
    },
    failPerfRun(id, { reason } = {}) {
      return kernel.mutate(data => {
        const run = data.runs.find(item => item.id === id)
        if (!run) throw new Error('采集运行不存在。')
        if (run.status !== 'running') throw new Error('该运行已结束。')
        run.status = 'failed'
        run.error = reason || '演示采集被手动标记失败'
        run.ended = stamp()
        kernel.activity(data, `${run.id} 采集失败：${run.error}`, 'red')
        return { id: run.id }
      })
    },
  }
}

// ---- 看板聚合（纯函数，供性能数据看板页与测试共用）----

// 按指标键聚合全部成功采集：时间正序点列、最新值与判定、回归次数；按最近活动倒序输出。
// 指标定义（名称/单位/方向/基线/阈值）取自最近一次成功采集的结果快照。
export function perfMetricSummary(snapshot) {
  const series = new Map()
  const completed = snapshot.runs.filter(run => run.status === 'success')
    .sort((a, b) => String(a.started).localeCompare(String(b.started)))
  for (const run of completed) {
    for (const result of run.results) {
      const entry = series.get(result.key) || { points: [], definition: null }
      entry.points.push({ runId: run.id, envId: run.envId, envName: run.envName, version: run.version, started: run.started, value: result.value, deltaPct: result.deltaPct, verdict: result.verdict })
      entry.definition = { key: result.key, name: result.name, unit: result.unit, direction: result.direction, baseline: result.baseline, threshold: result.threshold }
      series.set(result.key, entry)
    }
  }
  return [...series.entries()].map(([key, { points, definition }]) => ({
    ...definition, key, latest: points[points.length - 1], points,
    runCount: points.length, regressCount: points.filter(point => point.verdict === 'regress').length,
  })).sort((a, b) => String(b.latest.started).localeCompare(String(a.latest.started)))
}

// 按版本统计指定指标：均值、最优值、运行数，用于看板版本对比。
export function perfVersionStats(snapshot, metric) {
  if (!metric) return []
  const groups = new Map()
  for (const run of snapshot.runs.filter(item => item.status === 'success')) {
    const result = run.results.find(item => item.key === metric.key)
    if (!result) continue
    const group = groups.get(run.version) || { version: run.version, count: 0, total: 0, best: null, latest: null }
    group.count++
    group.total += result.value
    if (group.best === null || (metric.direction === 'lower' ? result.value < group.best : result.value > group.best)) group.best = result.value
    group.latest = result.value
    groups.set(run.version, group)
  }
  return [...groups.values()].map(group => {
    const avg = roundMetricValue(group.total / group.count, metric.baseline)
    return { ...group, avg, deltaPct: evaluateMetric(metric, avg).deltaPct }
  }).sort((a, b) => a.version.localeCompare(b.version))
}
