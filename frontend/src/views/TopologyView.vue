<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import Icon from '../components/Icon.vue'
import MetricCard from '../components/MetricCard.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { activeResources, editableEnvironment, idleDevice, memberships, sameIds, topologyPlan } from '../topology.js'

const props = defineProps({ snapshot: Object, save: Function, refresh: Function, mode: String })
const emit = defineEmits(['device', 'environment'])
const editing = ref(false), saving = ref(false), refreshing = ref(false), error = ref(''), success = ref('')
const draft = ref(null), selectedIds = ref([]), sourceId = ref(''), targetId = ref(''), operation = ref('move')
const query = ref(''), filter = ref('all'), arch = ref('all'), state = ref('all'), devicePage = ref(1), envPage = ref(1), view = ref('graph')
const viewport = ref(null), zoom = ref(1), positions = ref({}), dragging = ref('')
const current = computed(() => draft.value || props.snapshot)
const devices = computed(() => activeResources(current.value.devices))
const envs = computed(() => activeResources(current.value.environments))
const selected = computed(() => devices.value.filter(d => selectedIds.value.includes(d.id)))
const related = id => memberships(envs.value, id)
const typeName = type => type === 'virtual' ? '虚拟机' : '物理机'
const editable = env => editableEnvironment(env, current.value.devices)
const commonSources = computed(() => envs.value.filter(e => selectedIds.value.length && selectedIds.value.every(id => e.deviceIds.includes(id))))
const targetOptions = computed(() => envs.value.filter(e => !selected.value.length || selected.value.every(d => d.arch === e.arch && d.type === e.type)))
const plan = computed(() => {
  if (!editing.value || !selectedIds.value.length || !targetId.value) return { changes: [] }
  try { return topologyPlan(current.value, { deviceIds: selectedIds.value, sourceId: sourceId.value, targetId: targetId.value, mode: operation.value }) }
  catch (e) { return { changes: [], error: e.message } }
})
const changes = computed(() => plan.value.changes)
const relationChanges = computed(() => changes.value.flatMap(c => [
  ...c.expectedDeviceIds.filter(id => !c.deviceIds.includes(id)).map(deviceId => ({ envId: c.envId, deviceId, action: 'remove' })),
  ...c.deviceIds.filter(id => !c.expectedDeviceIds.includes(id)).map(deviceId => ({ envId: c.envId, deviceId, action: 'add' })),
]))
const dirty = computed(() => relationChanges.value.length > 0)
const stale = computed(() => !!draft.value && changes.value.some(c => {
  const env = props.snapshot.environments.find(e => e.id === c.envId && e.lifecycle !== 'retired')
  return !env || !sameIds(env.deviceIds, c.expectedDeviceIds)
}))
const filteredDevices = computed(() => devices.value.filter(d => {
  const refs = related(d.id)
  const text = `${d.name} ${d.id} ${d.ip || ''} ${refs.map(e => e.name).join(' ')}`.toLowerCase()
  return (!query.value.trim() || text.includes(query.value.trim().toLowerCase())) &&
    (filter.value !== 'shared' || refs.length > 1) && (filter.value !== 'unassigned' || !refs.length) &&
    (filter.value !== 'editable' || idleDevice(d)) && (arch.value === 'all' || d.arch === arch.value) &&
    (state.value === 'all' || (state.value === 'available' ? idleDevice(d) : !idleDevice(d)))
}))
const devicePages = computed(() => Math.max(1, Math.ceil(filteredDevices.value.length / 6)))
const visibleDevices = computed(() => filteredDevices.value.slice((devicePage.value - 1) * 6, devicePage.value * 6))
const filteredEnvs = computed(() => envs.value.filter(e => (editing.value || filter.value !== 'shared' || e.deviceIds.some(id => related(id).length > 1)) && (arch.value === 'all' || e.arch === arch.value) &&
  (state.value === 'all' || (state.value === 'available' ? editable(e) : !editable(e)))))
const envPages = computed(() => Math.max(1, Math.ceil(filteredEnvs.value.length / 3)))
const graphEnvs = computed(() => {
  if (!editing.value && filter.value === 'unassigned') return []
  if (editing.value && selectedIds.value.length) {
    const ids = new Set([...(operation.value === 'move' ? [sourceId.value] : []), targetId.value, ...selectedIds.value.flatMap(id => related(id).map(e => e.id))].filter(Boolean))
    return [...ids].map(id => envs.value.find(e => e.id === id)).filter(Boolean)
  }
  return filteredEnvs.value.slice((envPage.value - 1) * 3, envPage.value * 3)
})
const graphDevices = computed(() => {
  const ids = new Set([...graphEnvs.value.flatMap(e => e.deviceIds), ...selectedIds.value])
  if (filter.value === 'unassigned') visibleDevices.value.forEach(d => ids.add(d.id))
  return devices.value.filter(d => ids.has(d.id))
})
const graphWidth = computed(() => Math.max(940, graphEnvs.value.length * 300 + 40))
const graphRows = computed(() => Math.max(1, ...graphEnvs.value.map(env => Math.ceil(graphDevices.value.filter(d => related(d.id).length < 2 && env.deviceIds.includes(d.id)).length / 2))))
const graphHeight = computed(() => Math.max(590, 335 + graphRows.value * 120 + (graphDevices.value.some(d => related(d.id).length > 1 || !graphEnvs.value.some(e => e.deviceIds.includes(d.id))) ? 120 : 0)))
const envPosition = (env, i) => positions.value[env.id] || { x: 35 + i * ((graphWidth.value - 70) / Math.max(1, graphEnvs.value.length)), y: 65 }
function devicePosition(device, i) {
  if (positions.value[device.id]) return positions.value[device.id]
  const refs = graphEnvs.value.filter(e => e.deviceIds.includes(device.id))
  if (refs.length > 1) {
    const centers = refs.map(e => envPosition(e, graphEnvs.value.indexOf(e)).x + 125)
    return { x: centers.reduce((a, b) => a + b, 0) / centers.length - 68, y: 310 + graphRows.value * 120 }
  }
  const env = refs[0]
  if (!env) return { x: 30 + (i % 6) * 150, y: 310 + graphRows.value * 120 }
  const siblings = graphDevices.value.filter(d => env.deviceIds.includes(d.id) && graphEnvs.value.filter(e => e.deviceIds.includes(d.id)).length === 1)
  const index = siblings.findIndex(d => d.id === device.id)
  const base = envPosition(env, graphEnvs.value.indexOf(env))
  return { x: base.x - 15 + index % 2 * 145, y: 310 + Math.floor(index / 2) * 120 }
}
const nodePosition = (id, kind) => kind === 'env' ? envPosition(graphEnvs.value.find(e => e.id === id), graphEnvs.value.findIndex(e => e.id === id)) : devicePosition(graphDevices.value.find(d => d.id === id), graphDevices.value.findIndex(d => d.id === id))
const edges = computed(() => graphEnvs.value.flatMap(env => {
  const change = changes.value.find(c => c.envId === env.id)
  const before = env.deviceIds, after = change?.deviceIds || before
  return [...new Set([...before, ...after])].filter(id => graphDevices.value.some(d => d.id === id)).map(id => {
    const a = nodePosition(env.id, 'env'), b = nodePosition(id, 'device')
    const startX = a.x + 125, endX = b.x + 65, middleY = a.y + 170
    return { id: `${env.id}-${id}`, path: `M ${startX} ${a.y + 120} C ${startX} ${middleY}, ${endX} ${b.y - 65}, ${endX} ${b.y}`,
      action: !before.includes(id) ? 'add' : !after.includes(id) ? 'remove' : 'keep',
      selected: selectedIds.value.includes(id), shared: related(id).length > 1 }
  })
}))
const envName = id => envs.value.find(e => e.id === id)?.name || id
const deviceName = id => devices.value.find(d => d.id === id)?.name || id
const countAfter = env => changes.value.find(c => c.envId === env.id)?.deviceIds.length ?? env.deviceIds.length
const graphStyle = computed(() => ({ width: `${graphWidth.value * zoom.value}px`, height: `${graphHeight.value * zoom.value}px` }))
watch([query, filter, arch, state], () => { devicePage.value = 1; envPage.value = 1; positions.value = {} })
watch(filter, () => { if (!editing.value) selectedIds.value = [] })
watch(devicePages, n => { devicePage.value = Math.min(devicePage.value, n) })
watch(envPages, n => { envPage.value = Math.min(envPage.value, n) })
watch([sourceId, targetId, operation, selectedIds], () => { error.value = ''; positions.value = {} })

function updateSources() {
  const hadSource = sourceId.value
  if (!commonSources.value.some(e => e.id === sourceId.value)) sourceId.value = commonSources.value[0]?.id || ''
  if (!sourceId.value) operation.value = 'add'
  else if (!hadSource) operation.value = 'move'
  if (!targetOptions.value.some(e => e.id === targetId.value)) targetId.value = ''
}
function selectDevice(device) {
  if (ignoreClick) { ignoreClick = false; return }
  if (saving.value || (editing.value && !idleDevice(device))) return
  if (editing.value) {
    if (!selectedIds.value.includes(device.id) && selected.value.some(d => d.arch !== device.arch || d.type !== device.type)) {
      error.value = '请选择相同架构、类型的设备；可先取消当前选择。'; return
    }
    selectedIds.value = selectedIds.value.includes(device.id) ? selectedIds.value.filter(id => id !== device.id) : [...selectedIds.value, device.id]
  }
  else {
    selectedIds.value = [device.id]
    const env = related(device.id).find(e => filteredEnvs.value.some(f => f.id === e.id))
    if (env) envPage.value = Math.floor(filteredEnvs.value.findIndex(e => e.id === env.id) / 3) + 1
  }
  updateSources()
}
function startEditing() {
  draft.value = JSON.parse(JSON.stringify(props.snapshot)); editing.value = true; error.value = ''; success.value = ''
  selectedIds.value = selectedIds.value.filter(id => idleDevice(devices.value.find(d => d.id === id)))
  updateSources(); nextTick(fit)
}
function leave() {
  if (saving.value) return false
  if (dirty.value && !window.confirm('有未保存的拓扑变更，确定放弃并退出吗？')) return false
  editing.value = false; draft.value = null; targetId.value = ''; filter.value = 'all'; error.value = ''; positions.value = {}
  return true
}
defineExpose({ canLeave: () => !editing.value || leave() })
function undo() { if (!saving.value) { targetId.value = ''; error.value = ''; positions.value = {} } }
async function refreshData(rebase = false) {
  if (refreshing.value || saving.value) return
  if (rebase && dirty.value && !window.confirm('刷新将放弃当前预览，是否继续？')) return
  refreshing.value = true; error.value = ''
  try {
    await props.refresh()
    if (rebase) { draft.value = JSON.parse(JSON.stringify(props.snapshot)); targetId.value = ''; selectedIds.value = []; sourceId.value = '' }
  } catch (e) { error.value = e.message || '刷新失败，请重试。' }
  finally { refreshing.value = false }
}
async function saveChanges() {
  if (saving.value || !dirty.value || plan.value.error || stale.value) return
  saving.value = true; error.value = ''
  try {
    const count = relationChanges.value.length, focusId = targetId.value
    await props.save(plan.value)
    editing.value = false; draft.value = null; targetId.value = ''; filter.value = 'all'; positions.value = {}
    await nextTick()
    const index = filteredEnvs.value.findIndex(e => e.id === focusId)
    if (index >= 0) envPage.value = Math.floor(index / 3) + 1
    await nextTick(); fit()
    success.value = `已保存 ${count} 项关联变更，相关环境需重新健康复核。`
  } catch (e) { error.value = e.message || '保存失败，请刷新核对后重试。' }
  finally { saving.value = false }
}
function fit() {
  zoom.value = Math.min(1, Math.max(.75, ((viewport.value?.clientWidth || 960) - 16) / graphWidth.value))
  if (viewport.value) { viewport.value.scrollTop = 0; viewport.value.scrollLeft = 0 }
}
function dragStart(event, device) {
  if (!editing.value || !idleDevice(device) || saving.value) { event.preventDefault(); return }
  if (!selectedIds.value.includes(device.id)) selectedIds.value = [device.id]
  updateSources(); dragging.value = device.id
  event.dataTransfer.effectAllowed = 'move'; event.dataTransfer.setData('application/x-argus-device', device.id)
}
function dropDevice(event, env) {
  if (!dragging.value || saving.value) return
  event.preventDefault(); targetId.value = env.id; dragging.value = ''; nextTick(fit)
}
let movement = null, ignoreClick = false
function pointerDown(event, id, kind) {
  if (editing.value || event.button !== 0) return
  const p = nodePosition(id, kind)
  movement = { id, x: event.clientX, y: event.clientY, px: p.x, py: p.y, moved: false }
  event.currentTarget.setPointerCapture(event.pointerId)
}
function pointerMove(event) {
  if (!movement) return
  const dx = (event.clientX - movement.x) / zoom.value, dy = (event.clientY - movement.y) / zoom.value
  if (Math.abs(dx) + Math.abs(dy) < 6 && !movement.moved) return
  movement.moved = true
  positions.value = { ...positions.value, [movement.id]: { x: Math.max(0, Math.min(graphWidth.value - 255, movement.px + dx)), y: Math.max(0, Math.min(graphHeight.value - 125, movement.py + dy)) } }
}
function pointerUp() { ignoreClick = !!movement?.moved; movement = null }
function beforeUnload(event) { if (dirty.value) { event.preventDefault(); event.returnValue = '' } }
window.addEventListener('beforeunload', beforeUnload)
window.addEventListener('resize', fit)
onBeforeUnmount(() => { window.removeEventListener('beforeunload', beforeUnload); window.removeEventListener('resize', fit) })
nextTick(fit)
</script>

<template>
  <section class="topology-page" aria-label="环境拓扑">
    <div class="page-heading"><div><div class="eyebrow">RESOURCE TOPOLOGY</div><h1>{{ editing ? '编辑环境拓扑' : '环境拓扑' }}<span v-if="editing" class="badge tone-amber">{{ dirty ? '未保存' : '编辑中' }}</span></h1><p>{{ editing ? '选择设备和目标环境，预览关联变化后保存。' : '从设备出发，查看环境关联，快速调整资源组合。' }}</p></div><div class="heading-actions"><span class="badge" :class="mode === 'http' ? 'tone-teal' : 'tone-amber'">{{ mode === 'http' ? '后端数据' : '浏览器 Mock' }}</span><button v-if="editing" class="button secondary" :disabled="saving" @click="leave"><Icon name="logout" :size="17" />退出编辑</button><template v-else><button class="button secondary" :disabled="refreshing" @click="refreshData()"><Icon name="refresh" :size="17" />{{ refreshing ? '刷新中…' : '更新视图' }}</button><button class="button primary" @click="startEditing"><Icon name="edit" :size="17" />编辑拓扑</button></template></div></div>
    <div v-if="error || stale" class="topology-notice topology-error" role="alert"><Icon name="alert" :size="18" /><span>{{ error || '环境拓扑已变化，请刷新后重新调整。' }}</span><button v-if="editing" class="text-button" :disabled="refreshing || saving" @click="refreshData(true)">刷新并重新调整</button></div>
    <div v-if="success" class="topology-notice topology-success" role="status"><Icon name="check" :size="18" />{{ success }}</div>
    <div v-if="editing" class="topology-notice"><Icon name="move" :size="19" />拖拽设备到目标环境，或在右侧选择环境；保存后生效。</div>
    <div v-else class="metrics-grid topology-metrics"><MetricCard label="登记设备" :value="devices.length" detail="按唯一设备统计" icon="server" /><MetricCard label="测试环境" :value="envs.length" detail="当前资源池内环境" icon="box" /><MetricCard label="共享设备" :value="devices.filter(d => related(d.id).length > 1).length" detail="同时关联多个环境" icon="flow" tone="amber" /><MetricCard label="未关联设备" :value="devices.filter(d => !related(d.id).length).length" detail="已登记，尚未加入环境" icon="monitor" tone="teal" /></div>

    <div class="topology-workspace" :class="{ 'is-editing': editing }" :aria-busy="saving">
      <aside class="panel topology-inventory" aria-label="已登记设备">
        <div class="topology-panel-title"><h2>{{ editing ? '选择设备' : '已登记设备' }}</h2><span class="count-pill">{{ devices.length }}</span></div>
        <label class="search-field"><Icon name="search" :size="17" /><input v-model="query" aria-label="搜索拓扑设备" placeholder="搜索设备名称、ID 或 IP" /></label>
        <div class="tabs topology-device-tabs"><button :class="{ active: filter === 'all' }" @click="filter = 'all'">全部</button><button v-if="editing" :class="{ active: filter === 'editable' }" @click="filter = 'editable'">可调整</button><template v-else><button :class="{ active: filter === 'shared' }" @click="filter = 'shared'">共享</button><button :class="{ active: filter === 'unassigned' }" @click="filter = 'unassigned'">未关联</button></template></div>
        <div class="topology-device-list"><button v-for="device in visibleDevices" :key="device.id" class="topology-device-row" :class="{ selected: selectedIds.includes(device.id), locked: editing && !idleDevice(device) }" :disabled="saving || (editing && !idleDevice(device))" :aria-pressed="selectedIds.includes(device.id)" :aria-label="`选择设备 ${device.name}`" :draggable="editing && idleDevice(device)" @dragstart="dragStart($event, device)" @dragend="dragging = ''" @click="selectDevice(device)"><span v-if="editing" class="topology-checkbox"><Icon v-if="selectedIds.includes(device.id)" name="check" :size="13" /></span><span class="topology-device-symbol"><Icon name="server" :size="21" /></span><span class="topology-device-copy"><strong>{{ device.name }}</strong><small class="mono">{{ device.id }}<span v-if="device.ip"> · {{ device.ip }}</span></small><small>{{ related(device.id).length > 1 ? `关联 ${related(device.id).length} 个环境` : related(device.id)[0]?.name || '未关联环境' }}</small><small v-if="editing && !idleDevice(device)"><Icon name="lock" :size="11" />资源锁定，暂不可调整</small></span><StatusBadge :status="device.status" /></button><div v-if="!visibleDevices.length" class="topology-empty"><Icon name="search" :size="28" /><p>未找到符合条件的设备</p><button class="text-button" @click="query = ''; filter = 'all'; arch = 'all'; state = 'all'">清除筛选</button></div></div>
        <div class="topology-inventory-footer"><span>{{ filteredDevices.length ? (devicePage - 1) * 6 + 1 : 0 }}–{{ Math.min(devicePage * 6, filteredDevices.length) }} / {{ filteredDevices.length }} 台</span><div><button class="icon-button" aria-label="上一页设备" :disabled="devicePage === 1" @click="devicePage--"><Icon name="chevron" class="topology-back" :size="15" /></button><span>{{ devicePage }} / {{ devicePages }}</span><button class="icon-button" aria-label="下一页设备" :disabled="devicePage >= devicePages" @click="devicePage++"><Icon name="chevron" :size="15" /></button></div></div>
      </aside>

      <section class="panel topology-canvas-panel" aria-label="环境与设备关联">
        <div class="topology-canvas-toolbar"><h2>{{ editing ? '关联变化预览' : '环境与设备关联' }}</h2><div class="topology-tools"><div class="view-toggle"><button class="button compact" :class="view === 'graph' ? 'primary' : 'secondary'" :aria-pressed="view === 'graph'" @click="view = 'graph'; nextTick(fit)">拓扑</button><button class="button compact" :class="view === 'table' ? 'primary' : 'secondary'" :aria-pressed="view === 'table'" @click="view = 'table'">列表</button></div><template v-if="!editing"><select v-model="arch" aria-label="拓扑架构筛选"><option value="all">全部架构</option><option>ARM64</option><option>x86_64</option></select><select v-model="state" aria-label="拓扑状态筛选"><option value="all">全部状态</option><option value="available">空闲可调整</option><option value="locked">锁定资源</option></select></template><button class="icon-button" aria-label="适应拓扑视图" title="适应视图" @click="positions = {}; fit()"><Icon name="expand" :size="18" /></button></div></div>
        <div class="topology-graph-caption"><span>当前视图 {{ graphEnvs.length }} 个环境 · {{ graphDevices.length }} 台设备</span><div v-if="!editing" class="topology-env-pager"><button class="icon-button" aria-label="上一组环境" :disabled="envPage === 1" @click="envPage--; positions = {}; nextTick(fit)"><Icon name="chevron" class="topology-back" :size="14" /></button><span>{{ envPage }} / {{ envPages }}</span><button class="icon-button" aria-label="下一组环境" :disabled="envPage >= envPages" @click="envPage++; positions = {}; nextTick(fit)"><Icon name="chevron" :size="14" /></button></div></div>
        <div v-if="view === 'graph'" ref="viewport" class="topology-viewport"><div class="topology-scroll-size" :style="graphStyle"><div class="topology-graph" :style="{ width: `${graphWidth}px`, height: `${graphHeight}px`, transform: `scale(${zoom})` }"><svg class="topology-edges" :viewBox="`0 0 ${graphWidth} ${graphHeight}`" aria-label="环境与唯一设备之间的逻辑关联" role="img"><path v-for="edge in edges" :key="edge.id" :d="edge.path" :class="[edge.action, { selected: edge.selected, shared: edge.shared }]" /></svg>
          <button v-for="(env, i) in graphEnvs" :key="env.id" class="topology-env-node" :class="{ locked: !editable(env), target: editing && targetId === env.id, droppable: dragging && editable(env) }" :style="{ left: `${envPosition(env, i).x}px`, top: `${envPosition(env, i).y}px` }" :disabled="saving" :aria-label="`${editing ? '选择目标环境' : '查看环境'} ${env.name}`" @pointerdown="pointerDown($event, env.id, 'env')" @pointermove="pointerMove" @pointerup="pointerUp" @pointercancel="pointerUp" @dragover.prevent @drop="dropDevice($event, env)" @click="ignoreClick ? ignoreClick = false : editing ? targetId = env.id : emit('environment', env)"><span v-if="editing && targetId === env.id" class="topology-target-label">目标环境</span><span class="topology-env-icon"><Icon name="box" :size="25" /></span><div><strong>{{ env.name }}</strong><small class="mono">{{ env.id }}</small><small>{{ env.arch }} · {{ typeName(env.type) }}<Icon v-if="!editable(env)" name="lock" :size="12" /></small><span class="topology-node-count">{{ env.deviceIds.length }}<template v-if="countAfter(env) !== env.deviceIds.length"> → {{ countAfter(env) }}</template> 台设备<em v-if="countAfter(env) !== env.deviceIds.length" :class="countAfter(env) > env.deviceIds.length ? 'tone-blue' : 'tone-red'">{{ countAfter(env) > env.deviceIds.length ? '+' : '' }}{{ countAfter(env) - env.deviceIds.length }}</em></span></div></button>
          <button v-for="(device, i) in graphDevices" :key="device.id" class="topology-device-node" :class="{ selected: selectedIds.includes(device.id), shared: related(device.id).length > 1 }" :style="{ left: `${devicePosition(device, i).x}px`, top: `${devicePosition(device, i).y}px` }" :disabled="saving || (editing && !idleDevice(device))" :draggable="editing && idleDevice(device)" :aria-label="`选择设备 ${device.name}`" @dragstart="dragStart($event, device)" @dragend="dragging = ''" @pointerdown="pointerDown($event, device.id, 'device')" @pointermove="pointerMove" @pointerup="pointerUp" @pointercancel="pointerUp" @click="selectDevice(device)"><Icon name="server" :size="18" /><strong>{{ device.name }}</strong><small class="mono">{{ device.id }}</small><small class="mono">{{ device.ip || '地址未记录' }}</small><StatusBadge :status="device.status" /><span v-if="related(device.id).length > 1" class="topology-shared-label">共享 · {{ related(device.id).length }} 个环境</span></button>
          <div v-if="!graphDevices.length && !graphEnvs.length" class="topology-empty topology-graph-empty"><Icon name="flow" :size="38" /><h3>暂无符合条件的环境拓扑</h3><p>可选择左侧设备查看关联，或调整筛选条件。</p></div>
        </div></div></div>
        <div v-else class="topology-table table-scroll"><table class="data-table"><thead><tr><th>设备 / ID</th><th>状态</th><th>当前关联环境</th><th v-if="editing">待保存变化</th></tr></thead><tbody><tr v-for="device in graphDevices" :key="device.id"><td><button class="plain-title" :disabled="editing && !idleDevice(device)" @click="selectDevice(device)">{{ device.name }}</button><small class="mono">{{ device.id }}</small></td><td><StatusBadge :status="device.status" /></td><td><span v-for="env in related(device.id)" :key="env.id" class="topology-table-env">{{ env.name }}</span><span v-if="!related(device.id).length">未关联</span></td><td v-if="editing"><span v-for="change in relationChanges.filter(c => c.deviceId === device.id)" :key="`${change.envId}-${change.action}`" class="topology-table-env" :class="change.action === 'add' ? 'color-blue' : 'color-red'">{{ change.action === 'add' ? '+' : '−' }} {{ envName(change.envId) }}</span></td></tr><tr v-if="!graphDevices.length"><td :colspan="editing ? 4 : 3">暂无设备</td></tr></tbody></table></div>
        <div class="topology-canvas-footer"><div class="topology-legend"><span><i class="keep" />保留关联</span><span v-if="editing"><i class="add" />新增关联</span><span v-if="editing"><i class="remove" />移除关联</span><span v-else><i class="shared" />共享设备</span></div><div v-if="view === 'graph'" class="topology-zoom"><button aria-label="缩小拓扑" :disabled="zoom <= .35" @click="zoom = Math.max(.35, zoom - .1)">−</button><span>{{ Math.round(zoom * 100) }}%</span><button aria-label="放大拓扑" :disabled="zoom >= 1.5" @click="zoom = Math.min(1.5, zoom + .1)">+</button></div></div>
        <div v-if="!editing && selected[0]" class="topology-selection"><Icon name="server" :size="23" /><div><strong>{{ selected[0].name }}</strong><small>{{ selected[0].id }} · {{ selected[0].arch }} · {{ selected[0].memory || '规格未记录' }}</small><p>当前关联：{{ related(selected[0].id).map(e => e.name).join('、') || '未关联环境' }}</p></div><button class="text-button" @click="emit('device', selected[0])">查看设备详情<Icon name="arrow" :size="15" /></button></div>
      </section>

      <aside v-if="editing" class="panel topology-inspector" aria-label="调整设备关联"><div class="topology-panel-title"><h2>调整设备关联</h2><span class="count-pill">{{ selectedIds.length }} 台</span></div><div v-if="!selected.length" class="topology-empty"><Icon name="server" :size="32" /><h3>选择需要调整的设备</h3><p>可多选同一架构、类型的空闲设备。</p></div><template v-else><div class="topology-selected-summary"><Icon name="server" :size="26" /><div><strong>{{ selected.length === 1 ? selected[0].name : `已选择 ${selected.length} 台设备` }}</strong><small>{{ selected.map(d => d.id).join('、') }}</small><small>{{ selected[0].arch }} · {{ typeName(selected[0].type) }}</small></div></div><fieldset :disabled="saving" class="topology-operation"><legend>操作方式</legend><label><input v-model="operation" type="radio" value="move" :disabled="!commonSources.length" /><span><strong>迁移关联</strong><small>移除选定的原环境关联，加入目标环境</small></span></label><label><input v-model="operation" type="radio" value="add" /><span><strong>新增关联</strong><small>保留原关联，设备可被多个环境共享</small></span></label></fieldset><label v-if="operation === 'move'" class="topology-field">当前环境<select v-model="sourceId" :disabled="saving" aria-label="当前环境"><option value="" disabled>请选择当前环境</option><option v-for="env in commonSources" :key="env.id" :value="env.id">{{ env.name }} · {{ env.id }}</option></select></label><label class="topology-field">目标环境<select v-model="targetId" :disabled="saving" aria-label="目标环境"><option value="">请选择目标环境</option><option v-for="env in targetOptions" :key="env.id" :value="env.id" :disabled="!editable(env) || (operation === 'move' && sourceId === env.id)">{{ env.name }} · {{ env.id }}{{ !editable(env) ? ' · 已锁定' : '' }}</option></select></label><p v-if="plan.error" class="topology-inline-error" role="alert">{{ plan.error }}</p><div class="topology-change-list"><h3>变更清单<span class="count-pill">{{ relationChanges.length }}</span></h3><div v-for="change in relationChanges" :key="`${change.envId}-${change.deviceId}-${change.action}`" class="topology-change"><span :class="change.action === 'add' ? 'tone-blue' : 'tone-red'">{{ change.action === 'add' ? '+' : '−' }}</span><div><strong>{{ change.action === 'add' ? '加入' : '从' }} {{ envName(change.envId) }}{{ change.action === 'remove' ? '移除' : '' }}</strong><small>{{ deviceName(change.deviceId) }} · {{ change.deviceId }}</small></div></div><p v-if="!relationChanges.length" class="subtle-note">选择目标环境后，这里将显示关联变化。</p></div><div v-if="dirty && !stale" class="topology-checks"><p><Icon name="check" :size="14" />架构与类型匹配</p><p><Icon name="check" :size="14" />相关设备均空闲</p><p><Icon name="check" :size="14" />原环境保留有效设备</p></div><div class="topology-health-note"><Icon name="info" :size="17" /><span>调整后，相关环境需重新健康复核。</span></div></template><div class="topology-save-footer"><small>影响 {{ dirty ? selectedIds.length : 0 }} 台设备 · {{ changes.length }} 个环境</small><div><button class="button secondary compact" :disabled="saving || !targetId" @click="undo">撤销变更</button><button class="button primary compact" :disabled="saving || !dirty || stale" @click="saveChanges">{{ saving ? '正在保存…' : relationChanges.length ? `保存 ${relationChanges.length} 项变更` : '保存变更' }}</button></div></div></aside>
    </div>
    <p class="topology-footnote"><Icon name="info" :size="15" />同一设备可关联多个环境，占用以唯一设备为边界。{{ editing ? '使用中、维护、隔离或待复核的资源不可调整。' : '拖动节点仅调整布局，编辑拓扑后可修改设备关联。' }}</p>
  </section>
</template>
