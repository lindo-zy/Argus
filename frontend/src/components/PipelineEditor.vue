<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import Icon from './Icon.vue'
import { NODE_TYPES, autoLayout, cloneGraph, connectNodes, emptyGraph, exampleGraph, graphIssues, newNode, normalizeGraph, removeNode } from '../pipeline.js'
import '../pipeline.css'

const props = defineProps({ snapshot: Object, envs: Array, pipeline: Object, example: Boolean, save: Function, validate: Function, mode: String })
const emit = defineEmits(['close'])
const graph = ref(cloneGraph(props.pipeline?.graph || (props.example ? exampleGraph(props.snapshot.testCases || []) : emptyGraph())))
const name = ref(props.pipeline?.name || '操作系统验证流水线'), description = ref(props.pipeline?.description || '')
const arch = ref(props.pipeline?.arch || 'ARM64'), id = ref(props.pipeline?.id || ''), revision = ref(props.pipeline?.editRevision || 0)
const selectedId = ref(graph.value.nodes.find(n => n.type === 'cases')?.id || graph.value.nodes[0]?.id || '')
const selected = computed(() => graph.value.nodes.find(n => n.id === selectedId.value))
const catalog = computed(() => props.snapshot.testCases || [])
const currentPipeline = computed(() => props.snapshot.pipelines.find(p => p.id === id.value))
const stale = computed(() => !!id.value && (currentPipeline.value?.editRevision || 0) !== revision.value)
const payload = () => ({ ...(id.value ? { id: id.value, expectedRevision: revision.value } : {}), name: name.value, description: description.value, arch: arch.value, graph: cloneGraph(graph.value) })
const signature = () => JSON.stringify({ name: name.value, description: description.value, arch: arch.value, graph: graph.value })
const baseline = ref(props.example || !props.pipeline ? '' : signature())
const dirty = computed(() => signature() !== baseline.value)
const saving = ref(false), validating = ref(false), error = ref(''), message = ref(''), issues = ref([]), checked = ref(false)
const connecting = ref(''), targetId = ref(''), viewport = ref(null), zoom = ref(.85), nodeTab = ref('cases')
const canvasSize = computed(() => ({ width: Math.max(1000, ...graph.value.nodes.map(n => n.position.x + 300)), height: Math.max(540, ...graph.value.nodes.map(n => n.position.y + 210)) }))
const selectedCases = computed(() => (selected.value?.config.caseRefs || []).map(ref => catalog.value.find(c => c.id === ref.id && c.version === ref.version) || { ...ref, name: '用例已失效', module: '待重选' }))
const upstreamNames = computed(() => {
  if (!selected.value) return ''
  const visited = new Set(), installs = new Set()
  function visit(id) {
    if (visited.has(id)) return
    visited.add(id)
    const n = graph.value.nodes.find(n => n.id === id)
    if (n?.type === 'install') { installs.add(n.name); return }
    graph.value.edges.filter(e => e.target === id).forEach(e => visit(e.source))
  }
  visit(selected.value.id)
  return [...installs].join('、') || '尚未连接上游安装节点'
})
const edges = computed(() => graph.value.edges.map(edge => {
  const a = graph.value.nodes.find(n => n.id === edge.source), b = graph.value.nodes.find(n => n.id === edge.target)
  const x1 = a.position.x + 220, y1 = a.position.y + 65, x2 = b.position.x, y2 = b.position.y + 65
  const bend = Math.max(45, Math.abs(x2 - x1) / 2)
  return { ...edge, path: `M${x1},${y1} C${x1 + bend},${y1} ${x2 - bend},${y2} ${x2},${y2}`, x: (x1 + x2) / 2, y: (y1 + y2) / 2 }
}))
function note(text) { message.value = text; error.value = '' }
function mutate() { message.value = ''; checked.value = false; issues.value = [] }
function addNode(type, position) {
  if (graph.value.nodes.length >= 50) { error.value = '最多添加 50 个节点。'; return }
  const node = newNode(type, position || { x: Math.min(4800, 70 + graph.value.nodes.length % 3 * 290), y: Math.min(2800, 130 + Math.floor(graph.value.nodes.length / 3) * 185) })
  graph.value.nodes.push(node); selectedId.value = node.id; mutate()
}
function dragLibrary(event, type) { event.dataTransfer.setData('application/argus-node', type); event.dataTransfer.effectAllowed = 'copy' }
function dropNode(event) {
  const type = event.dataTransfer.getData('application/argus-node')
  if (!NODE_TYPES.some(n => n.type === type)) return
  const rect = viewport.value.getBoundingClientRect()
  addNode(type, { x: Math.max(0, Math.min(4780, (event.clientX - rect.left + viewport.value.scrollLeft) / zoom.value - 110)), y: Math.max(0, Math.min(2870, (event.clientY - rect.top + viewport.value.scrollTop) / zoom.value - 40)) })
}
let drag = null
function startDrag(event, node) {
  if (saving.value || event.button !== 0 || event.target.closest('button,input,select')) return
  selectedId.value = node.id
  drag = { id: node.id, x: event.clientX, y: event.clientY, position: { ...node.position }, element: event.currentTarget }
  event.currentTarget.setPointerCapture?.(event.pointerId)
}
function moveDrag(event) {
  if (!drag || saving.value) return
  const node = graph.value.nodes.find(n => n.id === drag.id)
  node.position = { x: Math.max(0, Math.min(4780, drag.position.x + (event.clientX - drag.x) / zoom.value)), y: Math.max(0, Math.min(2870, drag.position.y + (event.clientY - drag.y) / zoom.value)) }
  mutate()
}
function endDrag() { drag = null }
function connect(target) {
  try { graph.value = connectNodes(graph.value, connecting.value, target); connecting.value = ''; targetId.value = ''; mutate(); error.value = '' }
  catch (e) { error.value = e.message }
}
function selectNode(node) { selectedId.value = node.id; nodeTab.value = 'cases' }
function deleteSelected() {
  graph.value = removeNode(graph.value, selectedId.value); selectedId.value = graph.value.nodes[0]?.id || ''; connecting.value = ''; targetId.value = ''; mutate()
}
function duplicateSelected() {
  if (!selected.value) return
  const original = cloneGraph(selected.value)
  addNode(original.type, { x: Math.min(4780, original.position.x + 30), y: Math.min(2870, original.position.y + 155) })
  Object.assign(selected.value, { name: `${original.name.slice(0, 75)} 副本`, config: original.config })
}
async function fit() {
  await nextTick()
  if (viewport.value) zoom.value = Math.max(.35, Math.min(1, (viewport.value.clientWidth - 30) / canvasSize.value.width))
}
function layout() { graph.value = autoLayout(graph.value); mutate(); fit() }
function loadExample() {
  if (graph.value.nodes.length && !window.confirm('用示例替换当前画布？尚未保存的节点将被替换。')) return
  graph.value = exampleGraph(catalog.value); selectedId.value = graph.value.nodes[1]?.id || ''; mutate(); fit()
}
function clearGraph() {
  if (graph.value.nodes.length && !window.confirm('清空当前画布？保存前可通过返回放弃本次修改。')) return
  graph.value = emptyGraph(); selectedId.value = ''; connecting.value = ''; mutate()
}
async function validate() {
  error.value = ''; message.value = ''; validating.value = true
  try {
    const normalized = normalizeGraph(graph.value, catalog.value, arch.value)
    const local = graphIssues(normalized, props.envs, arch.value)
    const result = props.validate ? await props.validate(payload()) : { issues: local }
    issues.value = result.issues; checked.value = true
    if (!issues.value.length) note('编排校验通过。保存仍为草稿，不会执行任务或占用设备。')
  } catch (e) { error.value = e.message; checked.value = false }
  finally { validating.value = false }
}
async function save() {
  if (saving.value) return
  error.value = ''; message.value = ''; saving.value = true
  try {
    const normalized = normalizeGraph(graph.value, catalog.value, arch.value)
    const response = await props.save({ ...payload(), graph: normalized })
    const saved = response.snapshot.pipelines.find(p => p.id === response.result.id)
    id.value = saved.id; revision.value = saved.editRevision; name.value = saved.name; description.value = saved.description; graph.value = cloneGraph(saved.graph)
    baseline.value = signature(); note('草稿已保存，可返回列表后继续编辑。')
    if (response.persistenceWarning) error.value = '浏览器存储不可用，草稿仅保存在本次会话内；刷新前请导出草稿。'
  } catch (e) { error.value = e.message }
  finally { saving.value = false }
}
function exportDraft() {
  const url = URL.createObjectURL(new Blob([JSON.stringify(payload(), null, 2)], { type: 'application/json' }))
  const link = document.createElement('a'); link.href = url; link.download = 'argus-pipeline-draft.json'; link.click(); URL.revokeObjectURL(url)
}
function canLeave() { return !saving.value && (!dirty.value || window.confirm('编排尚未保存，确定离开并放弃修改？')) }
function close() { if (canLeave()) emit('close') }
function beforeUnload(event) { if (dirty.value) { event.preventDefault(); event.returnValue = '' } }
defineExpose({ canLeave })
onMounted(() => { fit(); window.addEventListener('beforeunload', beforeUnload) })
onBeforeUnmount(() => window.removeEventListener('beforeunload', beforeUnload))

// Selection is a temporary transaction: cancel never mutates the graph.
const picking = ref(false), pickNodeId = ref(''), chosen = ref([]), caseQuery = ref(''), category = ref('全部用例')
const categories = computed(() => ['全部用例', ...new Set(catalog.value.map(c => c.category))])
const visibleCases = computed(() => catalog.value.filter(c => c.arches.includes(arch.value) && (category.value === '全部用例' || c.category === category.value) && `${c.id} ${c.name} ${c.module}`.toLowerCase().includes(caseQuery.value.trim().toLowerCase())))
const pickedCases = computed(() => chosen.value.map(id => catalog.value.find(c => c.id === id)).filter(Boolean))
const allVisibleSelected = computed(() => visibleCases.value.length > 0 && visibleCases.value.every(c => chosen.value.includes(c.id)))
function openPicker() { pickNodeId.value = selected.value.id; chosen.value = selected.value.config.caseRefs.map(c => c.id); caseQuery.value = ''; category.value = '全部用例'; picking.value = true }
function toggleCase(id) { chosen.value = chosen.value.includes(id) ? chosen.value.filter(c => c !== id) : [...chosen.value, id] }
function toggleVisible() { chosen.value = allVisibleSelected.value ? chosen.value.filter(id => !visibleCases.value.some(c => c.id === id)) : [...new Set([...chosen.value, ...visibleCases.value.map(c => c.id)])] }
function confirmCases() {
  const node = graph.value.nodes.find(n => n.id === pickNodeId.value)
  if (node?.type === 'cases') { node.config.caseRefs = pickedCases.value.map(({ id, version }) => ({ id, version })); mutate() }
  picking.value = false
}
</script>

<template>
  <section class="pipeline-editor" aria-label="流水线 DAG 编排器">
    <header class="pe-heading">
      <div class="pe-heading-main"><button class="icon-button" aria-label="返回流水线列表" :disabled="saving" @click="close"><Icon name="arrow" class="pe-back" /></button><div><div class="eyebrow">PIPELINE STUDIO</div><div class="pe-title-line"><input v-model="name" aria-label="流水线名称" maxlength="120" :disabled="saving" @input="mutate" /><span class="badge tone-muted">草稿</span><span v-if="dirty" class="pe-unsaved">未保存</span></div></div></div>
      <div class="pe-actions"><span class="badge tone-blue">{{ mode === 'http' ? '后端数据' : '浏览器 Mock' }}</span><button class="button secondary small" :disabled="saving || validating" @click="validate"><Icon name="check" :size="16" />{{ validating ? '校验中…' : '校验编排' }}</button><button class="button primary small" :disabled="saving || picking || !dirty || stale" @click="save"><Icon name="download" :size="16" />{{ saving ? '保存中…' : '保存草稿' }}</button></div>
    </header>
    <div class="pe-meta"><label>目标架构<select v-model="arch" :disabled="saving" @change="mutate"><option>ARM64</option><option>x86_64</option></select></label><input v-model="description" aria-label="流水线描述" placeholder="添加编排说明（可选）" maxlength="200" :disabled="saving" @input="mutate" /><span>连线表示依赖 · 节点可重复添加</span></div>
    <div v-if="stale" class="notice-banner"><Icon name="alert" /><p>草稿已有新版本，当前内容已保留。请先导出草稿，再返回列表重新打开。<button class="text-button" @click="exportDraft">导出当前草稿</button></p></div>
    <div v-if="error" class="form-error pe-feedback" role="alert"><Icon name="alert" :size="17" />{{ error }}<button class="text-button" @click="exportDraft">导出草稿</button></div>
    <div v-if="message" class="pe-feedback pe-success" role="status"><Icon name="check" :size="17" />{{ message }}</div>
    <div v-if="checked && issues.length" class="pe-issues" role="alert"><strong>发现 {{ issues.length }} 项待完善，仍可保存草稿</strong><button v-for="(issue, i) in issues" :key="i" @click="selectedId = issue.nodeId || selectedId">{{ issue.message }}</button></div>
    <div class="pe-studio" :class="{ 'pe-picking': picking }">
      <aside class="pe-library"><div class="pe-panel-heading"><h2>节点库</h2><Icon name="box" :size="17" /></div><p>拖到画布，或点击添加</p><button v-for="kind in NODE_TYPES" :key="kind.type" class="pe-library-node" draggable="true" :disabled="saving || picking" @dragstart="dragLibrary($event, kind.type)" @click="addNode(kind.type)"><span class="pe-type-icon" :class="kind.type"><Icon :name="kind.icon" :size="21" /></span><span><strong>{{ kind.name }}</strong><small>{{ kind.description }}</small></span><span class="pe-grip">⠿</span></button><div class="pe-library-example"><span class="eyebrow">组合示例</span><div class="pe-mini-chain"><Icon name="server" /><Icon name="arrow" :size="14" /><Icon name="play" /><Icon name="arrow" :size="14" /><Icon name="play" /></div><p>环境安装 → 性能用例<br />→ 开源组件用例</p><button class="button secondary small" :disabled="saving || picking" @click="loadExample">载入示例</button></div><div class="pe-library-note"><Icon name="info" :size="15" /><p>“性能用例”等名称可自由修改，每个执行节点独立选用例。</p></div></aside>
      <section class="pe-canvas-panel" aria-label="编排画布"><header class="pe-canvas-header"><strong>编排画布 <span>{{ graph.nodes.length }} 节点 · {{ graph.edges.length }} 连线</span></strong><div><button class="icon-button" title="自动布局" aria-label="自动布局" :disabled="saving || picking" @click="layout"><Icon name="flow" :size="17" /></button><button class="icon-button" title="清空画布" aria-label="清空画布" :disabled="saving || picking" @click="clearGraph"><Icon name="close" :size="16" /></button></div></header>
        <div v-if="connecting" class="pe-connect-hint" role="status">从「{{ graph.nodes.find(n => n.id === connecting)?.name }}」连接：点击目标节点的左侧端点<button class="text-button" @click="connecting = ''">取消</button></div>
        <div ref="viewport" class="pe-viewport" @dragover.prevent @drop.prevent="dropNode" @click.self="connecting = ''"><div class="pe-extent" :style="{ width: `${canvasSize.width * zoom}px`, height: `${canvasSize.height * zoom}px` }"><div class="pe-world" :style="{ width: `${canvasSize.width}px`, height: `${canvasSize.height}px`, transform: `scale(${zoom})` }">
          <svg class="pe-edges" :width="canvasSize.width" :height="canvasSize.height" aria-label="节点依赖连线"><defs><marker id="pe-arrowhead" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 0 L8 4 L0 8" /></marker></defs><g v-for="edge in edges" :key="edge.id"><path :d="edge.path" class="pe-edge-path" marker-end="url(#pe-arrowhead)" /><foreignObject :x="edge.x - 13" :y="edge.y - 13" width="26" height="26"><button class="pe-delete-edge" :aria-label="`删除 ${graph.nodes.find(n => n.id === edge.source).name} 到 ${graph.nodes.find(n => n.id === edge.target).name} 的连线`" :disabled="saving || picking" title="删除连线" @click="graph.edges = graph.edges.filter(e => e.id !== edge.id); mutate()">×</button></foreignObject></g></svg>
          <article v-for="node in graph.nodes" :key="node.id" class="pe-node" :class="{ selected: selectedId === node.id, invalid: issues.some(i => i.nodeId === node.id) }" :style="{ left: `${node.position.x}px`, top: `${node.position.y}px` }" tabindex="0" :aria-label="`${node.name}，${node.type === 'install' ? '环境安装' : '用例执行'}`" @click="selectNode(node)" @keydown.enter="selectNode(node)" @keydown.delete.prevent="selectedId = node.id; deleteSelected()" @pointerdown="startDrag($event, node)" @pointermove="moveDrag" @pointerup="endDrag" @pointercancel="endDrag" @lostpointercapture="endDrag">
            <button class="pe-port pe-in" :class="{ ready: connecting && connecting !== node.id }" :disabled="!connecting || saving || picking" :aria-label="`连接到${node.name}`" @click.stop="connect(node.id)" />
            <div class="pe-node-heading"><span class="pe-type-icon" :class="node.type"><Icon :name="node.type === 'install' ? 'server' : 'play'" :size="21" /></span><div><strong>{{ node.name }}</strong><small>{{ node.type === 'install' ? '环境安装' : '用例执行' }}</small></div><span class="pe-grip">⠿</span></div>
            <template v-if="node.type === 'install'"><p>{{ envs.find(e => e.id === node.config.envId)?.name || '运行时选择环境' }}</p><small class="pe-node-detail">{{ node.config.imageRef || '待配置镜像引用' }}</small></template><template v-else><p>已选 <b>{{ node.config.caseRefs.length }}</b> 条用例 <button class="text-button" :disabled="saving || picking" @click.stop="selectNode(node); openPicker()">选择用例</button></p><small class="pe-node-detail">继承上游环境 · 节点内顺序执行</small></template>
            <button class="pe-port pe-out" :class="{ active: connecting === node.id }" :disabled="saving || picking" :aria-label="`从${node.name}开始连线`" @click.stop="connecting = connecting === node.id ? '' : node.id" />
          </article>
        </div></div><div v-if="!graph.nodes.length" class="pe-canvas-empty"><span class="pe-empty-symbol"><Icon name="flow" :size="36" /></span><h3>从一个节点开始</h3><p>添加环境安装和用例执行节点，连接成你的测试流程</p><button class="button secondary small" @click="loadExample">试试三阶段示例<Icon name="arrow" :size="15" /></button></div></div>
        <footer class="pe-canvas-footer"><div><button class="icon-button" aria-label="缩小画布" @click="zoom = Math.max(.35, zoom - .1)">−</button><span class="mono">{{ Math.round(zoom * 100) }}%</span><button class="icon-button" aria-label="放大画布" @click="zoom = Math.min(1.5, zoom + .1)">+</button><button class="icon-button" aria-label="适应画布" @click="fit"><Icon name="expand" :size="16" /></button></div><span>拖动节点 · 右端点 → 左端点连线</span></footer>
      </section>
      <aside class="pe-inspector"><div class="pe-panel-heading"><h2>节点配置</h2><div v-if="selected"><button class="icon-button" aria-label="复制当前节点" :disabled="saving || picking" @click="duplicateSelected"><Icon name="plus" :size="17" /></button><button class="icon-button" aria-label="删除当前节点" :disabled="saving || picking" @click="deleteSelected"><Icon name="close" :size="17" /></button></div></div>
        <template v-if="selected"><fieldset :disabled="saving || picking" class="pe-fields"><div class="pe-inspector-title"><span class="pe-type-icon" :class="selected.type"><Icon :name="selected.type === 'install' ? 'server' : 'play'" /></span><strong>{{ selected.name }}</strong></div><label>节点名称<input v-model="selected.name" maxlength="80" @input="mutate" /></label><template v-if="selected.type === 'install'"><label>目标环境<select v-model="selected.config.envId" @change="mutate"><option value="">运行时选择匹配环境</option><option v-for="env in envs.filter(e => e.arch === arch)" :key="env.id" :value="env.id">{{ env.name }} · {{ env.status === 'available' ? '空闲' : '占用或不可用' }}</option></select></label><label>镜像引用<input v-model="selected.config.imageRef" maxlength="200" placeholder="制品 URI 或镜像引用" @input="mutate" /></label><p class="subtle-note">编排阶段不占用环境；镜像引用的真实性与摘要将在执行接入时校验。</p></template><template v-else><div class="pe-node-tabs"><button :class="{ active: nodeTab === 'cases' }" @click="nodeTab = 'cases'">用例</button><button :class="{ active: nodeTab === 'strategy' }" @click="nodeTab = 'strategy'">执行策略</button></div><template v-if="nodeTab === 'cases'"><div class="pe-selected-heading"><strong>已选用例（{{ selectedCases.length }}）</strong><button class="button outline small" @click="openPicker"><Icon name="plus" :size="14" />选择用例</button></div><div class="pe-selected-cases"><div v-for="item in selectedCases" :key="item.id"><Icon name="check" :size="14" /><span>{{ item.name }}<small class="mono">{{ item.id }} · {{ item.version }}</small></span><button class="icon-button" :aria-label="`移除${item.name}`" @click="selected.config.caseRefs = selected.config.caseRefs.filter(c => c.id !== item.id); mutate()"><Icon name="close" :size="13" /></button></div><p v-if="!selectedCases.length" class="pe-no-cases">尚未选择用例<br /><span>从目录挑选当前节点要执行的用例</span></p></div></template><template v-else><label>节点内执行方式<select disabled><option>顺序执行</option></select></label><label>用例失败后<select v-model="selected.config.failure" @change="mutate"><option value="stop">停止后续节点</option><option value="continue">继续执行并保留失败结果</option></select></label></template><label class="pe-inherited">环境来源<span><Icon name="link" :size="14" />{{ upstreamNames }}</span></label></template>
          <div class="pe-connection-form"><strong>连接节点</strong><select v-model="targetId" aria-label="连接目标节点"><option value="">选择目标节点</option><option v-for="n in graph.nodes.filter(n => n.id !== selected.id)" :key="n.id" :value="n.id">{{ n.name }}</option></select><button class="button secondary small" :disabled="!targetId" @click="connecting = selected.id; connect(targetId)"><Icon name="link" :size="14" />建立依赖</button></div>
        </fieldset></template><div v-else class="pe-inspector-empty"><Icon name="settings" :size="32" /><p>选择画布节点<br />配置环境或挑选用例</p></div>
      </aside>
      <section v-if="picking" class="pe-case-picker" role="region" aria-label="选择用例" @keydown.esc.stop="picking = false"><header><div><span class="pe-type-icon cases"><Icon name="play" /></span><h2>为「{{ graph.nodes.find(n => n.id === pickNodeId)?.name }}」选择用例</h2><span class="badge tone-muted">仅应用于当前节点</span></div><button class="icon-button" aria-label="关闭用例选择" @click="picking = false"><Icon name="close" /></button></header><div class="pe-case-workbench"><nav aria-label="用例分类"><h3>用例目录</h3><button v-for="item in categories" :key="item" :class="{ active: category === item }" @click="category = item"><Icon name="box" :size="16" />{{ item }}</button></nav><div class="pe-case-list"><div class="pe-case-search"><label class="search-field"><Icon name="search" :size="17" /><input v-model="caseQuery" autofocus aria-label="搜索用例" placeholder="搜索用例名称 / ID" /></label><span class="badge tone-blue">{{ arch }}</span></div><div class="pe-case-table"><table class="data-table"><thead><tr><th><input type="checkbox" aria-label="选择当前筛选全部用例" :checked="allVisibleSelected" @change="toggleVisible" /></th><th>用例 ID</th><th>用例名称</th><th>分类 / 版本</th></tr></thead><tbody><tr v-for="item in visibleCases" :key="item.id" :class="{ 'pe-case-checked': chosen.includes(item.id) }"><td><input type="checkbox" :aria-label="`选择${item.name}`" :checked="chosen.includes(item.id)" @change="toggleCase(item.id)" /></td><td class="mono">{{ item.id }}</td><td><label @click="toggleCase(item.id)">{{ item.name }}</label><small>{{ item.module }}</small></td><td>{{ item.category }}<small>{{ item.version }}</small></td></tr></tbody></table><p v-if="!visibleCases.length" class="empty-state">没有匹配的用例，请调整搜索或分类。</p></div></div><aside class="pe-pick-summary"><h3>已选用例 · {{ chosen.length }}</h3><div class="pe-picked-items"><div v-for="item in pickedCases" :key="item.id"><span>{{ item.name }}<small class="mono">{{ item.id }} · {{ item.version }}</small></span><button class="icon-button" :aria-label="`取消选择${item.name}`" @click="toggleCase(item.id)"><Icon name="close" :size="14" /></button></div><p v-if="!chosen.length" class="subtle-note">勾选列表中的用例</p></div><p class="subtle-note"><Icon name="info" :size="14" />当前目录为演示用例，未接入真实用例中心。</p></aside></div><footer><span>已选 {{ chosen.length }} 条 · 取消不会更改节点</span><div><button class="button secondary small" @click="picking = false">取消</button><button class="button primary small" @click="confirmCases">确认选择（{{ chosen.length }}）</button></div></footer></section>
    </div>
    <div class="pe-bottom-note"><Icon name="info" :size="15" /><span>编排与用例目录为演示功能。保存只更新草稿，不连接 Jenkins、不安装环境、不执行用例；已发布配置可创建编排副本。</span></div>
  </section>
</template>
