// DAG editing contract shared by the canvas and browser Mock. No device operations.
export const NODE_TYPES = [
  { type: 'install', name: '环境安装', icon: 'server', description: '选择环境与安装镜像' },
  { type: 'cases', name: '用例执行', icon: 'play', description: '自选用例，设置执行策略' },
]
export const cloneGraph = value => JSON.parse(JSON.stringify(value))
export const emptyGraph = () => ({ schema: 1, nodes: [], edges: [] })
export function newNode(type, position = { x: 80, y: 180 }) {
  if (!NODE_TYPES.some(item => item.type === type)) throw new Error('请选择有效节点类型。')
  return { id: `node-${globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`}`, type,
    name: type === 'install' ? '环境安装' : '用例执行', position,
    config: type === 'install' ? { envId: '', imageRef: '' } : { caseRefs: [], mode: 'sequential', failure: 'stop' } }
}
const fail = message => { throw new Error(message) }
const text = (value, label, max, required = false) => {
  if (typeof value !== 'string' || value.length > max || (required && !value.trim())) fail(`${label}格式无效。`)
  return value.trim()
}
export function normalizeGraph(input, catalog, arch) {
  if (!input || input.schema !== 1 || !Array.isArray(input.nodes) || !Array.isArray(input.edges) || input.nodes.length > 50 || input.edges.length > 100) fail('编排格式无效，最多 50 个节点、100 条连线。')
  const ids = new Set()
  const nodes = input.nodes.map(node => {
    if (!node || !NODE_TYPES.some(item => item.type === node.type)) fail('编排包含未知节点类型。')
    const id = text(node.id, '节点 ID', 80, true)
    if (ids.has(id)) fail('节点 ID 不能重复。')
    ids.add(id)
    const p = node.position
    if (!p || !Number.isFinite(p.x) || !Number.isFinite(p.y) || p.x < 0 || p.x > 5000 || p.y < 0 || p.y > 3000) fail('节点坐标无效。')
    const config = node.config
    if (!config || typeof config !== 'object') fail('节点配置无效。')
    let normalized
    if (node.type === 'install') {
      if (Object.keys(config).some(k => !['envId', 'imageRef'].includes(k))) fail('环境安装配置包含未知参数。')
      normalized = { envId: text(config.envId, '环境', 64), imageRef: text(config.imageRef, '镜像引用', 200) }
    } else {
      if (Object.keys(config).some(k => !['caseRefs', 'mode', 'failure'].includes(k)) || config.mode !== 'sequential' || !['stop', 'continue'].includes(config.failure) || !Array.isArray(config.caseRefs) || config.caseRefs.length > 200) fail('用例执行配置无效。')
      const seen = new Set()
      const caseRefs = config.caseRefs.map(ref => {
        const item = catalog.find(c => c.id === ref?.id && c.version === ref?.version)
        if (!item) fail('用例不存在或版本已变化，请重新选择。')
        if (!item.arches.includes(arch)) fail('用例与流水线架构不匹配。')
        if (seen.has(item.id)) fail('同一节点不能重复选择用例。')
        seen.add(item.id)
        return { id: item.id, version: item.version }
      })
      normalized = { caseRefs, mode: 'sequential', failure: config.failure }
    }
    return { id, name: text(node.name, '节点名称', 80, true), type: node.type, position: { x: p.x, y: p.y }, config: normalized }
  })
  const pairs = new Set(), edgeIds = new Set()
  const edges = input.edges.map(edge => {
    if (!edge || !ids.has(edge.source) || !ids.has(edge.target)) fail('连线引用了不存在的节点。')
    const id = text(edge.id, '连线 ID', 180, true)
    const pair = JSON.stringify([edge.source, edge.target])
    if (edge.source === edge.target) fail('节点不能连接自身。')
    if (pairs.has(pair) || edgeIds.has(id)) fail('不能重复连接节点。')
    pairs.add(pair); edgeIds.add(id)
    return { id, source: edge.source, target: edge.target }
  })
  const graph = { schema: 1, nodes, edges }
  topologicalOrder(graph)
  return graph
}
export function topologicalOrder(graph) {
  const counts = new Map(graph.nodes.map(n => [n.id, 0]))
  const next = new Map(graph.nodes.map(n => [n.id, []]))
  for (const edge of graph.edges) { counts.set(edge.target, counts.get(edge.target) + 1); next.get(edge.source).push(edge.target) }
  const queue = graph.nodes.filter(n => counts.get(n.id) === 0).map(n => n.id), ordered = []
  while (queue.length) {
    const id = queue.shift(); ordered.push(id)
    for (const target of next.get(id)) { counts.set(target, counts.get(target) - 1); if (!counts.get(target)) queue.push(target) }
  }
  if (ordered.length !== graph.nodes.length) fail('连线会形成环，DAG 不允许循环依赖。')
  return ordered
}
export function connectNodes(graph, source, target) {
  if (source === target) fail('节点不能连接自身。')
  if (!graph.nodes.some(n => n.id === source) || !graph.nodes.some(n => n.id === target)) fail('请先选择有效节点。')
  if (graph.edges.some(e => e.source === source && e.target === target)) fail('不能重复连接节点。')
  const edge = { id: `edge-${source}-${target}`.slice(0, 180), source, target }
  const next = { ...graph, edges: [...graph.edges, edge] }
  topologicalOrder(next)
  return next
}
export function removeNode(graph, id) {
  return { ...graph, nodes: graph.nodes.filter(n => n.id !== id), edges: graph.edges.filter(e => e.source !== id && e.target !== id) }
}
export function graphIssues(graph, environments, arch) {
  const issues = []
  if (!graph.nodes.length) return [{ message: '请添加环境安装和用例执行节点。' }]
  if (!graph.nodes.some(n => n.type === 'cases')) issues.push({ message: '请至少添加一个用例执行节点。' })
  const incoming = id => graph.edges.filter(e => e.target === id).map(e => e.source)
  const roots = new Map()
  for (const id of topologicalOrder(graph)) {
    const node = graph.nodes.find(n => n.id === id)
    const parents = incoming(id)
    if (node.type === 'install') {
      roots.set(id, new Set([id]))
      if (parents.length) issues.push({ nodeId: id, message: `${node.name}：环境安装应作为分支起点。` })
      if (!node.config.imageRef) issues.push({ nodeId: id, message: `${node.name}：请填写镜像引用。` })
      const env = environments.find(e => e.id === node.config.envId && e.lifecycle !== 'retired')
      if (node.config.envId && (!env || env.arch !== arch)) issues.push({ nodeId: id, message: `${node.name}：所选环境已移出或架构不匹配。` })
    } else {
      const sources = new Set(parents.flatMap(parent => [...(roots.get(parent) || [])]))
      roots.set(id, sources)
      if (sources.size !== 1) issues.push({ nodeId: id, message: `${node.name}：请连接且仅继承一个上游安装环境。` })
      if (!node.config.caseRefs.length) issues.push({ nodeId: id, message: `${node.name}：请至少选择一条用例。` })
    }
  }
  return issues
}
export function autoLayout(graph) {
  const levels = new Map(), rows = new Map()
  const nodes = new Map(graph.nodes.map(n => [n.id, n]))
  const placed = new Map()
  for (const id of topologicalOrder(graph)) {
    const parents = graph.edges.filter(e => e.target === id).map(e => e.source)
    const level = parents.length ? Math.max(...parents.map(p => levels.get(p))) + 1 : 0
    const row = rows.get(level) || 0; levels.set(id, level); rows.set(level, row + 1)
    placed.set(id, { ...nodes.get(id), position: { x: 70 + level * 290, y: 160 + row * 190 } })
  }
  return { ...graph, nodes: graph.nodes.map(n => placed.get(n.id)) }
}
export function exampleGraph(catalog) {
  const install = newNode('install', { x: 70, y: 190 })
  install.config.imageRef = 'demo://os-25.03/build-108'
  const performance = newNode('cases', { x: 360, y: 190 }); performance.name = '性能用例'
  performance.config.caseRefs = catalog.filter(c => c.category === '性能测试').slice(0, 3).map(({ id, version }) => ({ id, version }))
  const components = newNode('cases', { x: 650, y: 190 }); components.name = '开源组件用例'
  components.config.caseRefs = catalog.filter(c => c.category === '开源组件').map(({ id, version }) => ({ id, version }))
  return { schema: 1, nodes: [install, performance, components], edges: [
    { id: 'example-1', source: install.id, target: performance.id }, { id: 'example-2', source: performance.id, target: components.id },
  ] }
}
