import test from 'node:test'
import assert from 'node:assert/strict'
import { createMockApi } from '../src/mock/api.js'
import { createSeed, DATA_KEY } from '../src/mock/seed.js'
import { autoLayout, connectNodes, exampleGraph, graphIssues, newNode, normalizeGraph, removeNode, topologicalOrder } from '../src/pipeline.js'
import { createHttpApi } from '../src/api/createHttpApi.js'

const catalog = createSeed().testCases
const ref = (id = 'PERF-001') => {
  const item = catalog.find(c => c.id === id)
  return { id: item.id, version: item.version }
}
function validGraph() {
  return { schema: 1, nodes: [
    { id: 'n1', type: 'install', name: '环境安装', position: { x: 70, y: 160 }, config: { envId: '', imageRef: 'demo://os/build-108' } },
    { id: 'n2', type: 'cases', name: '性能用例', position: { x: 360, y: 160 }, config: { caseRefs: [ref()], mode: 'sequential', failure: 'stop' } },
  ], edges: [{ id: 'e1', source: 'n1', target: 'n2' }] }
}
function fixture() {
  const data = createSeed()
  const storage = new Map([[DATA_KEY, JSON.stringify(data)]])
  return { data, api: createMockApi({ latency: 0, storage: { getItem: k => storage.get(k), setItem: (k, v) => storage.set(k, v) } }) }
}

test('example graph is locally valid and passes normalize plus issue check', () => {
  const example = exampleGraph(catalog)
  const normalized = normalizeGraph(example, catalog, 'ARM64')
  assert.deepEqual(graphIssues(normalized, createSeed().environments, 'ARM64'), [])
  assert.deepEqual(topologicalOrder(normalized).length, 3)
})

test('normalizeGraph rejects unknown types, duplicate ids, drift cases and oversized graphs', () => {
  const bad = validGraph(); bad.nodes[1].type = 'script'
  assert.throws(() => normalizeGraph(bad, catalog, 'ARM64'), /未知节点类型/)
  const dup = validGraph(); dup.nodes[1].id = 'n1'
  assert.throws(() => normalizeGraph(dup, catalog, 'ARM64'), /不能重复/)
  const drift = validGraph(); drift.nodes[1].config.caseRefs = [{ id: 'PERF-001', version: 'v999' }]
  assert.throws(() => normalizeGraph(drift, catalog, 'ARM64'), /版本已变化/)
  const driftArch = validGraph(); driftArch.nodes[1].config.caseRefs = [ref()]
  assert.doesNotThrow(() => normalizeGraph(driftArch, catalog, 'x86_64'))
  const extra = validGraph(); extra.nodes[1].config.job = 'free-job'
  assert.throws(() => normalizeGraph(extra, catalog, 'ARM64'), /配置无效/)
  const oversized = { schema: 1, edges: [], nodes: Array.from({ length: 51 }, (_, i) => (
    { id: `n${i}`, type: 'cases', name: `节点${i}`, position: { x: 0, y: 0 }, config: { caseRefs: [], mode: 'sequential', failure: 'stop' } })) }
  assert.throws(() => normalizeGraph(oversized, catalog, 'ARM64'), /50 个节点/)
})

test('topologicalOrder detects cycles and connectNodes guards self, duplicate and cycle edges', () => {
  const cycled = validGraph(); cycled.edges.push({ id: 'e2', source: 'n2', target: 'n1' })
  assert.throws(() => topologicalOrder(cycled), /循环依赖/)
  const graph = normalizeGraph(validGraph(), catalog, 'ARM64')
  assert.throws(() => connectNodes(graph, 'n1', 'n1'), /连接自身/)
  assert.throws(() => connectNodes(graph, 'n1', 'n2'), /不能重复连接/)
  const third = newNode('cases', { x: 650, y: 160 }); third.id = 'n3'
  const bigger = { ...graph, nodes: [...graph.nodes, third] }
  const twice = connectNodes(bigger, 'n1', 'n3')
  assert.deepEqual(twice.edges.map(e => `${e.source}->${e.target}`), ['n1->n2', 'n1->n3'])
  assert.throws(() => connectNodes(twice, 'n2', 'n1'), /循环依赖/)
  assert.throws(() => connectNodes(twice, 'n9', 'n1'), /有效节点/)
})

test('removeNode drops attached edges; autoLayout arranges levels and rows', () => {
  const graph = normalizeGraph(validGraph(), catalog, 'ARM64')
  const removed = removeNode(graph, 'n1')
  assert.deepEqual(removed.edges, [])
  const third = newNode('cases'); third.id = 'n3'
  const branch = { ...graph, nodes: [...graph.nodes, third], edges: [...graph.edges, { id: 'e2', source: 'n2', target: 'n3' }] }
  const laidOut = autoLayout(branch)
  const byId = Object.fromEntries(laidOut.nodes.map(n => [n.id, n.position]))
  assert.ok(byId.n1.x < byId.n2.x && byId.n2.x < byId.n3.x)
  assert.equal(byId.n2.y, byId.n3.y)
})

test('graphIssues flags missing cases, missing image and ambiguous upstream environment', () => {
  assert.deepEqual(graphIssues({ schema: 1, nodes: [], edges: [] }, [], 'ARM64'), [{ message: '请添加环境安装和用例执行节点。' }])
  const casesOnly = { schema: 1, nodes: [newNode('cases')], edges: [] }
  assert.ok(graphIssues(casesOnly, [], 'ARM64').some(i => /至少选择一条用例/.test(i.message)))
  const bare = validGraph(); bare.nodes[0].config.imageRef = ''
  assert.ok(graphIssues(bare, createSeed().environments, 'ARM64').some(i => /镜像引用/.test(i.message)))
  const envMismatch = validGraph(); envMismatch.nodes[0].config.envId = 'ENV-ARM-01'
  assert.ok(graphIssues(envMismatch, createSeed().environments, 'x86_64').some(i => /架构不匹配/.test(i.message)))
  assert.ok(graphIssues(validGraph(), createSeed().environments, 'ARM64').every(i => !/继承|镜像/.test(i.message)))
})

test('mock savePipeline stores draft with revision and rejects published, stale and unknown keys', async () => {
  const { api, data } = fixture()
  const created = await api.savePipeline({ name: '编排草稿', description: '', arch: 'ARM64', graph: validGraph() })
  assert.match(created.result.id, /^PL-/)
  assert.equal(created.result.editRevision, 1)
  const stored = created.snapshot.pipelines.find(p => p.id === created.result.id)
  assert.equal(stored.graph.nodes[0].type, 'install')
  assert.match(stored.xml, /orchestration/)
  const updated = await api.savePipeline({ id: created.result.id, expectedRevision: 1, name: '编排草稿二', description: '', arch: 'ARM64', graph: validGraph() })
  assert.equal(updated.result.editRevision, 2)
  await assert.rejects(api.savePipeline({ id: created.result.id, expectedRevision: 1, name: '并发冲突', description: '', arch: 'ARM64', graph: validGraph() }),
    e => e.code === 'pipeline_conflict')
  await assert.rejects(api.savePipeline({ id: 'os-smoke-test', expectedRevision: 0, name: '改已发布', description: '', arch: 'ARM64', graph: validGraph() }), /已发布配置不可修改/)
  await assert.rejects(api.savePipeline({ name: '带 Job', description: '', arch: 'ARM64', graph: validGraph(), job: 'free' }), /未知参数/)
  assert.equal(created.snapshot.pipelines.length, data.pipelines.length + 1)
})

test('mock validatePipeline returns issues for structural problems without mutating data', async () => {
  const { api, data } = fixture()
  const bare = validGraph(); bare.nodes[0].config.imageRef = ''
  const result = await api.validatePipeline({ arch: 'ARM64', graph: bare })
  assert.equal(result.valid, false)
  assert.ok(result.issues.some(i => /镜像引用/.test(i.message)))
  const cycled = validGraph(); cycled.edges.push({ id: 'e2', source: 'n2', target: 'n1' })
  await assert.rejects(api.validatePipeline({ arch: 'ARM64', graph: cycled }), /循环依赖/)
  assert.equal(data.revision, 0)
})

test('HTTP adapter posts draft payloads with actor header and preserves conflict code', async () => {
  const calls = [], snapshot = createSeed()
  const api = createHttpApi({ storage: null, fetch: async (url, options) => {
    calls.push({ url, options })
    const body = url.endsWith('/api/pipelines/validate')
      ? { valid: true, issues: [], order: ['n1', 'n2'], source: 'demo' }
      : { result: { id: 'PL-1', editRevision: 1 }, snapshot }
    return { ok: true, json: async () => body }
  } })
  const saved = await api.savePipeline({ name: '编排草稿', arch: 'ARM64', graph: validGraph() })
  assert.ok(calls[0].url.endsWith('/api/pipelines/drafts'))
  assert.equal(calls[0].options.headers['X-Argus-User'], 'U-1001')
  assert.equal(saved.result.id, 'PL-1')
  const validated = await api.validatePipeline({ arch: 'ARM64', graph: validGraph() })
  assert.ok(calls[1].url.endsWith('/api/pipelines/validate'))
  assert.equal(validated.valid, true)
  assert.deepEqual(calls[1].options.body, JSON.stringify({ arch: 'ARM64', graph: validGraph() }))
  const denied = createHttpApi({ storage: null, fetch: async () => ({ ok: false, status: 400, json: async () => ({ detail: '草稿已被其他操作更新', code: 'pipeline_conflict' }) }) })
  await assert.rejects(denied.savePipeline({ id: 'PL-1', expectedRevision: 0 }), e => e.code === 'pipeline_conflict')
})

test('copy works for draft and published pipelines; publish freezes draft, browser refuses Jenkins execution', async () => {
  const { api } = fixture()
  const created = await api.savePipeline({ name: '复制源', arch: 'ARM64', graph: validGraph() })
  const copied = await api.copyPipeline(created.result.id)
  assert.notEqual(copied.result.id, created.result.id)
  const p = copied.snapshot.pipelines.find(p => p.id === copied.result.id)
  assert.deepEqual(p.graph, validGraph())
  const released = await api.publishPipeline(p.id, { expectedRevision: p.editRevision, contentHash: p.contentHash })
  assert.equal(released.snapshot.pipelines.find(x => x.id === p.id).status, 'published')
  await assert.rejects(api.savePipeline({ id: p.id, name: p.name, arch: p.arch, graph: p.graph, expectedRevision: p.editRevision }), /已发布/)
  const second = await api.copyPipeline(p.id)
  assert.equal(second.snapshot.pipelines.find(x => x.id === second.result.id).status, 'draft')
  assert.throws(() => api.startRun({ pipelineId: p.id, envId: 'ENV-ARM-01', executionMode: 'jenkins', requestKey: 'one' }), /浏览器 Mock 不会调度/)
})

test('HTTP copy/publish/run preserve exact payload and replay key', async () => {
  const seen = []
  const api = createHttpApi({ storage: null, fetch: async (url, options) => {
    seen.push({ url, body: options.body && JSON.parse(options.body) })
    return { ok: true, json: async () => ({ result: {}, snapshot: createSeed() }) }
  } })
  await api.copyPipeline('PL-copy')
  await api.publishPipeline('PL-copy', { expectedRevision: 3, contentHash: 'hash' })
  const payload = { pipelineVersionId: 'PV-1', envId: 'ENV-ARM-01', executionMode: 'jenkins', requestKey: 'same-key' }
  await api.startRun(payload); await api.startRun(payload)
  assert.ok(seen[0].url.endsWith('/api/pipelines/PL-copy/copy'))
  assert.deepEqual(seen[1].body, { expectedRevision: 3, contentHash: 'hash' })
  assert.deepEqual(seen[2].body, payload); assert.deepEqual(seen[3].body, payload)
})
