<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import Icon from './components/Icon.vue'
import StatusBadge from './components/StatusBadge.vue'
import ModalShell from './components/ModalShell.vue'
import EnvironmentPicker from './components/EnvironmentPicker.vue'
import ResourceEditor from './components/ResourceEditor.vue'
import EnvironmentsView from './views/EnvironmentsView.vue'
import DashboardView from './views/DashboardView.vue'
import PipelinesView from './views/PipelinesView.vue'
import WorkspaceView from './views/WorkspaceView.vue'
import TopologyView from './views/TopologyView.vue'
import './topology.css'
import { useTheme } from './composables/useTheme.js'
import { createMockApi } from './mock/api.js'
import { createHttpApi } from './api/createHttpApi.js'
import { createSeed, enrichEnvironments, environmentIdsFor, deviceIdsFor } from './mock/seed.js'

const navGroups = [{ label: '总览', items: [{ id: 'dashboard', label: '总览', icon: 'home' }] }, { label: '资源管理', items: [{ id: 'environments', label: '环境资源', icon: 'box' }, { id: 'devices', label: '设备资产', icon: 'monitor' }, { id: 'topology', label: '环境拓扑', icon: 'flow' }, { id: 'leases', label: '借用管理', icon: 'lock' }, { id: 'maintenance', label: '维护中心', icon: 'shield' }] }, { label: '流水线编排', items: [{ id: 'pipelines', label: '标准流水线', icon: 'flow' }, { label: '升级流水线', icon: 'download', planned: true }, { label: '性能流水线', icon: 'play', planned: true }] }, { label: '数据平台', items: [{ id: 'runs', label: '测试执行记录', icon: 'clipboard' }, { id: 'quality', label: '质量分析', icon: 'chart' }, { label: '性能数据看板', icon: 'pulse', planned: true }] }, { label: '平台管理', items: [{ id: 'people', label: '人员管理', icon: 'user', adminOnly: true }] }]
const navigation = navGroups.flatMap(g => g.items)
const getPage = () => navigation.some(n => n.id === location.hash.slice(2)) ? location.hash.slice(2) : 'environments'
const page = ref(getPage()), mobileOpen = ref(false)
const currentTitle = computed(() => navigation.find(n => n.id === page.value)?.label)
const currentGroup = computed(() => navGroups.find(g => g.items.some(n => n.id === page.value))?.label)
const { preference, resolved, presentation } = useTheme()
const themeOptions = [{ id: 'light', label: '浅色', icon: 'sun' }, { id: 'dark', label: '深色', icon: 'moon' }, { id: 'system', label: '系统', icon: 'monitor' }]
let storage
try { storage = window.localStorage } catch { storage = null }
// 优先连接真实后端；后端不可达时自动回退浏览器 Mock（离线演示模式）。
const mockApi = createMockApi({ storage })
let api = createHttpApi({ base: (typeof import.meta !== 'undefined' && import.meta.env?.VITE_API_BASE) || 'http://127.0.0.1:8000' })
const topologyView = ref(null), dataMode = ref('http')
const snapshot = ref(createSeed()), loading = ref(true), loadError = ref(''), busy = ref(false), modalError = ref('')
const envs = computed(() => enrichEnvironments(snapshot.value))
const poolSnapshot = computed(() => ({ ...snapshot.value, devices: snapshot.value.devices.filter(d => d.lifecycle !== 'retired') }))
const currentUser = computed(() => snapshot.value.users?.find(u => u.id === snapshot.value.currentUserId) || null)
const isAdmin = computed(() => currentUser.value?.role === 'admin')
const visibleNavGroups = computed(() => navGroups.map(g => ({ ...g, items: g.items.filter(item => !item.adminOnly || isAdmin.value) })).filter(g => g.items.length))
const retiredEnvs = computed(() => snapshot.value.environments.filter(e => e.lifecycle === 'retired'))
const borrowResult = ref(null), resourceEditorDraft = ref(null), environmentReturnDraft = ref(null)
const editedResource = computed(() => (modal.value?.kind === 'deviceEdit' ? snapshot.value.devices : snapshot.value.environments).find(item => item.id === modal.value?.id))
const archiveTarget = ref(null)
const resourceEditingLocked = computed(() => {
  const resource = editedResource.value
  if (!resource) return false
  const ids = modal.value?.kind === 'deviceEdit' ? [resource.id] : resource.deviceIds
  return snapshot.value.devices.some(d => ids.includes(d.id) && ['leased','running','maintenance','checking','cleaning','reconciling'].includes(d.status))
})
const modal = ref(null), resetConfirm = ref(false), toast = ref(null)
let toastTimer
const selectedEnv = computed(() => envs.value.find(e => e.id === modal.value?.id))
const selectedLease = computed(() => snapshot.value.leases.find(e => e.id === modal.value?.id))
const selectedRun = computed(() => snapshot.value.runs.find(e => e.id === modal.value?.id))
const selectedMaintenance = computed(() => snapshot.value.maintenance.find(e => e.id === modal.value?.id))
const selectedPipeline = computed(() => snapshot.value.pipelines.find(e => e.id === modal.value?.id))
const selectedIssue = computed(() => snapshot.value.issues.find(e => e.id === modal.value?.id))
const form = ref({ envId: '', envIds: [], hours: 4, purpose: '', reason: '' })
const userForm = ref({ name: '', role: 'member' })
const borrowSelectionInvalid = computed(() => !form.value.envIds?.length)
const availableEnvs = computed(() => envs.value.filter(e => e.status === 'available'))
const pipelineEnvs = computed(() => availableEnvs.value.filter(e => e.arch === selectedPipeline.value?.arch))
const searchQuery = ref(''), searchOpen = ref(false), searchInput = ref(null)
const searchResults = computed(() => {
  const q = searchQuery.value.trim().toLowerCase()
  if (!q) return []
  const match = (id, name) => `${id} ${name}`.toLowerCase().includes(q)
  return [
    ...envs.value.filter(e => match(e.id, e.name)).slice(0, 4).map(e => ({ id: e.id, name: e.name, kind: 'environment', icon: 'box', label: '环境' })),
    ...snapshot.value.runs.filter(r => match(r.id, r.name)).slice(0, 3).map(r => ({ id: r.id, name: r.name, kind: 'run', icon: 'play', label: '执行' })),
    ...poolSnapshot.value.devices.filter(d => match(d.id, d.name)).slice(0, 2).map(d => ({ id: d.id, name: d.name, kind: 'device', icon: 'monitor', label: '设备' })),
  ]
})
const date = value => new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })
const envName = id => snapshot.value.environments.find(e => e.id === id)?.name || id
const scopeName = record => environmentIdsFor(record).map(envName).join('、')
function notify(message, tone = 'green') { clearTimeout(toastTimer); toast.value = { message, tone }; toastTimer = setTimeout(() => { toast.value = null }, 4500) }
async function load() {
  if (topologyView.value && !topologyView.value.canLeave()) return
  loading.value = true; loadError.value = ''
  try { const result = await api.load(); snapshot.value = result.snapshot; if (result.persistenceWarning) notify('本地存储不可用或数据已损坏，当前使用内存演示数据。', 'amber') }
  catch (error) {
    if (error?.unavailable && api !== mockApi) { api = mockApi; dataMode.value = 'mock'; notify('后端服务不可达，已切换到浏览器 Mock 演示模式。', 'amber'); return await load() }
    loadError.value = error.message || '数据加载失败，请重试。'
  }
  finally { loading.value = false }
}
async function refreshTopology() {
  const result = await api.load()
  snapshot.value = result.snapshot
}
async function saveTopology(payload) {
  const result = await api.saveTopology(payload)
  snapshot.value = result.snapshot
  return result
}
async function callApi(name, ...args) {
  if (typeof api[name] !== 'function') throw new Error('该功能需要连接后端服务（当前为浏览器 Mock 模式）。')
  return api[name](...args)
}
async function action(operation, message, close = true) {
  if (busy.value) return
  busy.value = true; modalError.value = ''
  try { const result = await operation(); snapshot.value = result.snapshot; if (close) modal.value = null; notify(message); return result }
  catch (error) { modalError.value = error.message || '操作未完成，请重试。' }
  finally { busy.value = false }
}
function navigate(id) { if (page.value === 'topology' && id !== 'topology' && topologyView.value && !topologyView.value.canLeave()) return; page.value = id; location.hash = `/${id}`; mobileOpen.value = false; searchOpen.value = false; modal.value = null }
function routeChange() { if (page.value === 'topology' && getPage() !== 'topology' && topologyView.value && !topologyView.value.canLeave()) { location.hash = '/topology'; return } page.value = getPage(); mobileOpen.value = false; presentation.value = false; window.scrollTo({ top: 0 }) }
function focusMain() { document.getElementById('main-content')?.focus() }
watch(page, () => { document.title = `${currentTitle.value} · Argus` }, { immediate: true })
// 人员管理仅管理员可见：普通人员直接输入 #/people 时重定向回环境资源。
watch(() => [page.value, isAdmin.value], () => { if (page.value === 'people' && !isAdmin.value) navigate('environments') })
function open(kind, id = null) { modalError.value = ''; resetConfirm.value = false; modal.value = { kind, id } }
function details(env) { open('environment', env.id) }
function runDetails(run) { open('run', run.id) }
function leaseDetails(lease) { open('lease', lease.id) }
function borrow(env) { form.value = { envIds: env ? [env.id] : [], hours: 4, purpose: '', reason: '' }; open('borrow') }
function maintenance(record) {
  if (record?.id && snapshot.value.maintenance.some(m => m.id === record.id)) { open('maintenanceDetail', record.id); return }
  if (record?.id) {
    const existing = snapshot.value.maintenance.find(m => ['maintenance', 'isolated'].includes(m.status) && (environmentIdsFor(m).includes(record.id) || deviceIdsFor(m, snapshot.value).some(id => record.deviceIds.includes(id))))
    if (existing) { open('maintenanceDetail', existing.id); return }
    details(record); return
  }
  form.value = { envId: availableEnvs.value[0]?.id || '', reason: '' }; open('maintenance')
}
function pipeline(config) { form.value = { envId: availableEnvs.value.find(e => e.arch === config.arch)?.id || '' }; open('pipeline', config.id) }
function closeModal() {
  if (busy.value) return
  if (modal.value?.kind === 'deviceEdit' && environmentReturnDraft.value) {
    resourceEditorDraft.value = environmentReturnDraft.value; environmentReturnDraft.value = null; open('environmentEdit'); return
  }
  modal.value = null
}
function editResource(kind, resource = null) {
  if (!resource && !isAdmin.value) { notify('新增环境 / 设备仅管理员可操作，请切换管理员身份。', 'amber'); return }
  resourceEditorDraft.value = null; environmentReturnDraft.value = null; open(kind === 'device' ? 'deviceEdit' : 'environmentEdit', resource?.id)
}
function addDeviceForEnvironment(draft) {
  if (!isAdmin.value) { notify('新增设备仅管理员可操作，请切换管理员身份。', 'amber'); return }
  environmentReturnDraft.value = draft; resourceEditorDraft.value = { arch: draft.arch, type: draft.type }; open('deviceEdit')
}
async function saveResource(payload) {
  const kind = modal.value.kind
  const result = await action(() => kind === 'deviceEdit' ? api.saveDevice(payload) : api.saveEnvironment(payload), payload.id ? '资源信息已更新，无需审批。' : '资源已加入资源池，可立即使用。')
  if (result && kind === 'deviceEdit' && environmentReturnDraft.value) {
    const draft = environmentReturnDraft.value
    resourceEditorDraft.value = { ...draft, deviceIds: [...new Set([...draft.deviceIds, ...(draft.arch === result.result.arch && draft.type === result.result.type ? [result.result.id] : [])])] }
    environmentReturnDraft.value = null; open('environmentEdit')
  }
}
function confirmArchive(kind, resource) { archiveTarget.value = { kind, id: resource.id, name: resource.name }; open('archiveResource') }
async function archiveResource() {
  const target = archiveTarget.value
  await action(() => target.kind === 'device' ? api.retireDevice(target.id) : api.archiveEnvironment(target.id), '已移出资源池，历史借用与执行记录仍保留。')
}
const restoreTarget = ref(null)
function confirmRestore(env) { restoreTarget.value = env; open('restoreResource') }
async function doRestore() {
  const target = restoreTarget.value
  await action(() => callApi('restoreEnvironment', target.id), `已将 ${target.name} 恢复入池。`)
}
function openTimeline(env) { open('timeline', env.id) }
const timelineEnv = computed(() => snapshot.value.environments.find(e => e.id === modal.value?.id))
const timelineItems = computed(() => {
  const env = timelineEnv.value
  if (!env) return []
  const items = []
  for (const lease of snapshot.value.leases) {
    if (environmentIdsFor(lease).includes(env.id)) items.push({ time: lease.start, kind: '借用', ref: lease.id, detail: `${lease.user} · ${lease.purpose || '未填写用途'} · ${lease.status}`, tone: 'blue' })
  }
  for (const run of snapshot.value.runs) {
    if (run.envId === env.id) items.push({ time: run.started, kind: '执行', ref: run.id, detail: `${run.name} · ${run.status}${run.result ? ' · ' + run.result : ''}`, tone: 'teal' })
  }
  for (const record of snapshot.value.maintenance) {
    if (environmentIdsFor(record).includes(env.id)) items.push({ time: record.date, kind: '维护', ref: record.id, detail: `${(record.reason || '').slice(0, 44)} · ${record.status}`, tone: 'amber' })
  }
  return items.sort((a, b) => String(b.time || '').localeCompare(String(a.time || ''))).slice(0, 50)
})
const pendingAddList = ref([]), pendingRemove = ref([]), pendingAdd = ref('')
watch(() => modal.value?.kind, () => { pendingAddList.value = []; pendingRemove.value = []; pendingAdd.value = '' })
const adjustCandidates = computed(() => {
  const env = selectedEnv.value
  if (!env) return []
  return poolSnapshot.value.devices.filter(d => d.lifecycle !== 'retired' && d.arch === env.arch && d.type === env.type && d.status === 'available' && !env.deviceIds.includes(d.id) && !pendingAddList.value.includes(d.id))
})
const adjustDirty = computed(() => pendingAddList.value.length > 0 || pendingRemove.value.length > 0)
function addPendingDevice() { if (pendingAdd.value) { pendingAddList.value.push(pendingAdd.value); pendingAdd.value = '' } }
function toggleRemoveDevice(deviceId) {
  pendingRemove.value = pendingRemove.value.includes(deviceId) ? pendingRemove.value.filter(i => i !== deviceId) : [...pendingRemove.value, deviceId]
}
async function saveAdjustDevices() {
  const env = selectedEnv.value
  await action(() => callApi('adjustEnvironmentDevices', env.id, { add: pendingAddList.value, remove: pendingRemove.value }), '设备关联已更新，历史借用快照不受影响。')
}
function sharedRefs(device) {
  const env = selectedEnv.value
  if (!env) return []
  return snapshot.value.environments.filter(e => e.lifecycle !== 'retired' && e.id !== env.id && e.deviceIds.includes(device.id))
}
const topoHeight = computed(() => (selectedEnv.value?.devices.length || 0) * 36 + 16)
const topoCenterY = computed(() => topoHeight.value / 2)
// ---- P1：利用率 / 手动健康检查 / 事件流 ----
const utilizationData = ref(null), utilizationNote = ref('')
watch(() => (modal.value?.kind === 'environment' ? selectedEnv.value?.id : ''), id => {
  utilizationData.value = null; utilizationNote.value = ''
  if (!id) return
  if (typeof api.utilization !== 'function') { utilizationNote.value = '浏览器 Mock 模式不提供利用率统计，连接后端后可用。'; return }
  api.utilization(id).then(data => { utilizationData.value = data }).catch(() => { utilizationNote.value = '利用率数据加载失败。' })
})
async function runHealthCheck(healthy) {
  const env = selectedEnv.value
  await action(() => callApi('healthCheck', env.id, { healthy, note: healthy ? '界面手动检查通过' : '界面手动检查异常' }),
    healthy ? '健康检查通过，设备保持可用。' : '检查异常，设备已隔离并建立维护记录。')
}
const eventsData = ref([]), eventsLoading = ref(false), flowCounts = ref(null)
async function fetchEvents() {
  eventsLoading.value = true; eventsData.value = []; flowCounts.value = null
  try {
    const data = await callApi('events', { limit: 40 })
    eventsData.value = data.events || []
    if (typeof api.request === 'function') {
      try { flowCounts.value = (await api.request('/api/reports/status-flow?days=7')).counts } catch { flowCounts.value = null }
    }
  } catch (error) { notify(error.message || '事件加载失败。', 'amber') }
  finally { eventsLoading.value = false }
}
watch(() => modal.value?.kind === 'eventsStream', open => { if (open) fetchEvents() })
function openEvents() { open('eventsStream') }
// ---- P2：导出 / 复制 / 模板 / 批量 ----
function exportEnvironments(filters = {}) {
  if (typeof api.exportUrl !== 'function') { notify('浏览器 Mock 模式不支持导出，连接后端后可用。', 'amber'); return }
  window.open(api.exportUrl(filters), '_blank')
}
function copyEnvironment(env) {
  if (!isAdmin.value) { notify('复制环境属于新增操作，仅管理员可执行。', 'amber'); return }
  resourceEditorDraft.value = { ...env, name: `${env.name} 副本`, deviceIds: [], tags: (env.tags || []).filter(t => t !== env.arch && t !== '物理机' && t !== '虚拟机') }
  open('environmentEdit')
}
const TEMPLATE_KEY = 'argus.envTemplates'
function loadTemplates() { try { return JSON.parse(localStorage.getItem(TEMPLATE_KEY) || '[]') } catch { return [] } }
const envTemplates = ref(loadTemplates())
function saveTemplate(draft) {
  const entry = { name: draft.name || '未命名模板', arch: draft.arch, type: draft.type, version: draft.version, description: draft.description, location: draft.location, tagInput: draft.tagInput || '', savedAt: new Date().toLocaleString('zh-CN', { hour12: false }) }
  envTemplates.value = [entry, ...envTemplates.value.filter(t => t.name !== entry.name)].slice(0, 10)
  try { localStorage.setItem(TEMPLATE_KEY, JSON.stringify(envTemplates.value)); notify(`模板「${entry.name}」已保存。`) } catch { notify('浏览器存储不可用，模板未保存。', 'amber') }
}
const batchTarget = ref(null), batchOutcomes = ref([])
async function runBatch(kind, ids = []) {
  if (!ids.length) return
  busy.value = true
  const outcomes = []
  for (const id of ids) {
    const name = (kind === 'retire' ? snapshot.value.environments : retiredEnvs.value).find(e => e.id === id)?.name || id
    try { await callApi(kind === 'retire' ? 'archiveEnvironment' : 'restoreEnvironment', id); outcomes.push({ envId: id, name, status: 'done', reason: kind === 'retire' ? '已移出资源池' : '已恢复入池' }) }
    catch (error) { outcomes.push({ envId: id, name, status: 'skipped', reason: error.message }) }
  }
  const fresh = await api.load(); snapshot.value = fresh.snapshot
  busy.value = false
  batchOutcomes.value = outcomes; batchTarget.value = kind; open('batchResult')
}
function toggleCol(key) { listCols.value = { ...listCols.value, [key]: !listCols.value[key] } }
const listCols = ref({ arch: true, type: true, owner: false, version: false, location: false, tags: false, devices: true, status: true, ip: true, ...(() => { try { return JSON.parse(localStorage.getItem('argus.envCols') || '') } catch { return {} } })() })
watch(listCols, cols => { try { localStorage.setItem('argus.envCols', JSON.stringify(cols)) } catch { /* 忽略存储失败 */ } }, { deep: true })
function retrySkipped() { form.value.envIds = borrowResult.value.outcomes.filter(item => item.status === 'skipped').map(item => item.envId); open('borrow') }
async function submitUserAdd() { const result = await action(() => api.addUser(userForm.value), `已新增人员 ${userForm.value.name}。`); if (result) userForm.value = { name: '', role: 'member' } }
function switchUser(user) { action(() => api.switchUser(user.id), `演示身份已切换为 ${user.name}（${user.role === 'admin' ? '管理员' : '普通人员'}）。`) }
const roleLabel = user => user?.role === 'admin' ? '管理员' : '普通人员'

function pickSearch(result) {
  searchOpen.value = false; searchQuery.value = ''
  if (result.kind === 'environment') { navigate('environments'); details(envs.value.find(e => e.id === result.id)) }
  else if (result.kind === 'run') { navigate('runs'); runDetails(snapshot.value.runs.find(r => r.id === result.id)) }
  else { navigate('devices'); details(envs.value.find(e => e.deviceIds.includes(result.id))) }
}
async function submitBorrow() {
  if (busy.value) return
  busy.value = true; modalError.value = ''
  try {
    const response = await api.borrow(form.value)
    snapshot.value = response.snapshot; borrowResult.value = response.result; open('borrowResult')
  } catch (error) { modalError.value = error.message || '申请未完成，请重试。' }
  finally { busy.value = false }
}
async function submitMaintenance() { await action(() => api.startMaintenance(form.value), '已创建维护，环境与共享设备状态已更新。') }
async function startPipeline() {
  const result = await action(() => api.startRun({ envId: form.value.envId, pipelineId: selectedPipeline.value.id }), '演示任务已创建，设备已锁定。')
  if (result) { navigate('runs'); runDetails(result.result) }
}
function togglePresentation() {
  presentation.value = !presentation.value
  window.scrollTo({ top: 0 })
}
function keydown(event) {
  if (event.key === 'Escape') { searchOpen.value = false; mobileOpen.value = false; if (!modal.value) presentation.value = false }
  if ((event.metaKey || event.ctrlKey) && event.key === 'k' && !modal.value) { event.preventDefault(); searchInput.value?.focus(); searchOpen.value = true }
}
// 只读配置内容来自快照中的流水线版本 XML（契约形声明式配置），不在界面层拼装。
const configXml = computed(() => selectedPipeline.value?.xml || '')
const modalTitle = computed(() => ({ environment: selectedEnv.value?.name, borrow: '申请借用环境', lease: '借用记录', maintenance: '创建维护', maintenanceDetail: '维护与健康复核', run: '执行详情', pipeline: '流水线配置', info: '指标口径与数据范围', settings: '工作空间设置', issue: '问题追溯', borrowResult: '环境借用结果', environmentEdit: editedResource.value ? '编辑环境' : '新增环境', deviceEdit: editedResource.value ? '编辑设备' : '新增设备', archiveResource: '移出资源池', identity: '演示身份切换', userAdd: '新增人员', restoreResource: '恢复入池', timeline: `历史时间线 · ${timelineEnv.value?.name || ''}`, eventsStream: '资源事件流', batchResult: '批量操作结果' })[modal.value?.kind] || '')
onMounted(() => { load(); window.addEventListener('hashchange', routeChange); window.addEventListener('keydown', keydown) })
onBeforeUnmount(() => { clearTimeout(toastTimer); window.removeEventListener('hashchange', routeChange); window.removeEventListener('keydown', keydown) })
</script>

<template>
  <div class="app-shell" :class="{ 'presentation-mode': presentation, 'mobile-open': mobileOpen }">
    <a href="#main-content" class="skip-link" @click.prevent="focusMain">跳到主要内容</a>
    <button v-if="mobileOpen" class="sidebar-backdrop" aria-label="收起导航" @click="mobileOpen = false" />
    <aside class="sidebar" aria-label="主导航"><a class="brand" href="#/environments"><svg viewBox="0 0 48 48" aria-hidden="true"><path d="M24 3 43 14v20L24 45 5 34V14Z" fill="none" stroke="currentColor" stroke-width="3" /><path d="M11 24s5-9 13-9 13 9 13 9-5 9-13 9-13-9-13-9Z" fill="currentColor" opacity=".2" /><circle cx="24" cy="24" r="5" fill="currentColor" /></svg><div><strong>ARGUS</strong><span>操作系统测试平台</span></div></a>
      <nav><div class="nav-group" v-for="group in visibleNavGroups" :key="group.label"><div class="nav-label">{{ group.label }}</div><template v-for="item in group.items" :key="item.id || item.label"><span v-if="item.planned" class="nav-item planned" aria-disabled="true" :title="`${item.label}（规划中）`"><Icon :name="item.icon" :size="21" /><span>{{ item.label }}</span><i class="nav-plan-tag">规划中</i></span><a v-else :href="`#/${item.id}`" :class="['nav-item', { active: page === item.id }]" :aria-current="page === item.id ? 'page' : undefined" :title="item.label" @click="mobileOpen = false"><Icon :name="item.icon" :size="21" /><span>{{ item.label }}</span><i v-if="item.id === 'maintenance'" class="nav-notice" /></a></template></div></nav>
      <div class="sidebar-bottom"><div class="workspace-chip"><span class="workspace-symbol">OS</span><div><strong>系统测试</strong><small>研发部 · 演示工作空间</small></div><Icon name="down" :size="15" /></div><button class="nav-item settings-button" aria-label="工作空间设置" @click="open('settings')"><Icon name="settings" :size="21" /><span>工作空间设置</span></button><div class="sidebar-version"><i class="dot teal" />{{ dataMode === 'http' ? 'HTTP API' : 'LOCAL MOCK' }}<span>v0.1.0</span></div></div>
    </aside>
    <div class="app-main"><header class="topbar"><div class="breadcrumb"><button class="icon-button mobile-menu" aria-label="打开导航" :aria-expanded="mobileOpen" @click="mobileOpen = !mobileOpen"><Icon name="menu" /></button><Icon name="home" :size="17" /><span>{{ currentGroup }}</span><span class="breadcrumb-slash">/</span><strong>{{ currentTitle }}</strong></div>
      <div class="topbar-tools"><div class="global-search" @focusout="event => { if (!event.currentTarget.contains(event.relatedTarget)) searchOpen = false }"><label class="search-field"><Icon name="search" :size="17" /><input ref="searchInput" v-model="searchQuery" aria-label="全局搜索" placeholder="搜索环境、设备或任务…" @focus="searchOpen = true" @keydown.enter.prevent="searchResults[0] && pickSearch(searchResults[0])" /><kbd>⌘ K</kbd></label><div v-if="searchOpen && searchQuery.trim()" class="search-popover"><div class="search-popover-label">搜索结果</div><button v-for="result in searchResults" :key="result.id" @click="pickSearch(result)"><Icon :name="result.icon" :size="18" /><span><strong>{{ result.name }}</strong><small class="mono">{{ result.id }}</small></span><span class="subtle-note">{{ result.label }}</span></button><div v-if="!searchResults.length" class="search-no-result">未找到匹配的环境、设备或任务</div></div></div>
        <div class="theme-toggle" role="group" aria-label="主题切换"><button v-for="item in themeOptions" :key="item.id" :class="{ active: preference === item.id }" :aria-pressed="preference === item.id" :title="item.id === 'system' ? '跟随系统' : `${item.label}主题`" @click="preference = item.id"><Icon :name="item.icon" :size="16" /><span>{{ item.label }}</span></button></div><button class="user-button" aria-label="演示身份切换" @click="open('identity')"><span class="avatar">{{ currentUser?.name?.slice(0, 1) || '?' }}</span><span>{{ currentUser?.name }}</span><span class="badge" :class="isAdmin ? 'tone-amber' : 'tone-blue'">{{ roleLabel(currentUser) }}</span><Icon name="down" :size="13" /></button></div>
    </header>
    <main id="main-content" class="page-content" tabindex="-1"><div v-if="loading" class="loading-state" role="status"><span class="spinner" /><h2>正在准备工作空间</h2><p>加载环境与执行数据…</p></div><div v-else-if="loadError" class="empty-state panel" role="alert"><Icon name="alert" :size="36" /><h2>暂时无法加载数据</h2><p>{{ loadError }}</p><button class="button primary" @click="load">重新加载</button></div><template v-else>
      <div v-show="page === 'environments'"><EnvironmentsView :envs="envs" :snapshot="poolSnapshot" :retired="retiredEnvs" :can-admin="isAdmin" :cols="listCols" @details="details" @borrow="borrow" @run="runDetails" @lease="leaseDetails" @navigate="navigate" @maintenance="maintenance" @create="editResource('environment')" @restore="confirmRestore" @timeline="openTimeline" @export="exportEnvironments" @events="openEvents" @col-toggle="toggleCol" @batch-operate="p => runBatch(p.kind, p.ids)" /></div>
      <div v-show="page === 'dashboard'"><DashboardView :envs="envs" :snapshot="poolSnapshot" :presentation="presentation" @navigate="navigate" @run="runDetails" @info="open('info')" @presentation="togglePresentation" /></div>
      <div v-show="page === 'pipelines'"><PipelinesView :snapshot="poolSnapshot" :envs="envs" @pipeline="pipeline" /></div>
      <TopologyView v-if="page === 'topology'" ref="topologyView" :snapshot="poolSnapshot" :mode="dataMode" :save="saveTopology" :refresh="refreshTopology" @environment="details" @device="device => editResource('device', device)" />
      <WorkspaceView v-if="!['environments', 'dashboard', 'pipelines', 'topology'].includes(page)" :page="page" :envs="envs" :snapshot="poolSnapshot" :current-user="currentUser" :can-admin="isAdmin" @details="details" @run="runDetails" @lease="leaseDetails" @borrow="borrow" @maintenance="maintenance" @issue="issue => open('issue', issue.id)" @device-create="editResource('device')" @device-edit="device => editResource('device', device)" @device-remove="device => confirmArchive('device', device)" @user-add="open('userAdd')" @user-role="(user, role) => action(() => api.setUserRole(user.id, role), `已将 ${user.name} 设为${roleLabel({ role })}。`)" @user-status="(user, status) => action(() => api.setUserStatus(user.id, status), `已${status === 'disabled' ? '停用' : '启用'} ${user.name}。`)" @user-switch="switchUser" />
      <footer class="app-footer"><span><i class="dot teal" />ARGUS · 本地演示工作空间</span><span>演示数据 · 未接入真实设备与外部系统</span><button class="text-button" @click="load"><Icon name="refresh" :size="13" />刷新数据</button></footer>
    </template></main></div>
  </div>

  <Transition name="toast"><div v-if="toast" class="toast-message" :class="`tone-${toast.tone}`" role="status"><Icon :name="toast.tone === 'green' ? 'check' : 'info'" :size="19" /><span>{{ toast.message }}</span><button class="icon-button" aria-label="关闭提示" @click="toast = null"><Icon name="close" :size="16" /></button></div></Transition>

  <ModalShell v-if="modal" :key="modal.kind" :title="modalTitle" :subtitle="modal.kind === 'info' ? '所有数值均为演示样本，不代表生产结果。' : '本地 MOCK · 所有操作只作用于演示数据'" :wide="['environment', 'borrow', 'borrowResult', 'environmentEdit', 'deviceEdit', 'run', 'pipeline', 'info', 'timeline', 'eventsStream', 'batchResult'].includes(modal.kind)" :drawer="['environment', 'run'].includes(modal.kind)" @close="closeModal">
    <p v-if="modalError" class="form-error" role="alert"><Icon name="alert" :size="17" />{{ modalError }}</p>
    <template v-if="modal.kind === 'environment' && selectedEnv"><div class="detail-summary"><div class="resource-icon"><Icon name="server" :size="30" /></div><div><span class="mono">{{ selectedEnv.id }}</span><p>{{ selectedEnv.description }}</p></div><StatusBadge :status="selectedEnv.status" /></div><dl class="detail-grid"><div><dt>处理器架构</dt><dd>{{ selectedEnv.arch }}</dd></div><div><dt>环境类型</dt><dd>{{ selectedEnv.type === 'physical' ? '物理机' : '虚拟机' }}</dd></div><div><dt>IP 地址</dt><dd class="mono">{{ selectedEnv.ip || '未记录' }}</dd></div><div v-if="selectedEnv.type === 'virtual'"><dt>宿主机</dt><dd class="mono">{{ selectedEnv.host || '未记录' }}</dd></div><div><dt>操作系统</dt><dd>{{ selectedEnv.version || '未记录' }}</dd></div><div><dt>设备数量</dt><dd>{{ selectedEnv.deviceIds.length }} 台</dd></div><div><dt>部署位置</dt><dd>{{ selectedEnv.location }}</dd></div><div><dt>负责人</dt><dd>{{ selectedEnv.owner || '未记录' }}</dd></div><div><dt>标签</dt><dd>{{ (selectedEnv.tags || []).join('、') || '未记录' }}</dd></div><div><dt>最近健康检查</dt><dd class="mono">{{ selectedEnv.health }}</dd></div></dl><div v-if="selectedEnv.shared" class="info-banner"><Icon name="link" :size="18" /><p>此环境包含共享设备；任一关联设备被占用，所有引用环境都将同步锁定。</p></div><h3 class="section-title">关联设备<button class="text-button" style="margin-left:auto" @click="openTimeline(selectedEnv)"><Icon name="clock" :size="14" />历史时间线</button></h3><div class="table-scroll"><table class="data-table"><thead><tr><th>设备</th><th>规格</th><th>状态</th><th v-if="!resourceEditingLocked">调整</th></tr></thead><tbody><tr v-for="device in selectedEnv.devices" :key="device.id" :style="pendingRemove.includes(device.id) ? 'opacity:.45' : undefined"><td class="mono">{{ device.id }}<small>{{ device.name }}</small><div v-for="other in sharedRefs(device)" :key="other.id"><button class="inline-link shared-ref" @click="details(envs.find(e => e.id === other.id))">↳ {{ other.name }}</button></div></td><td>{{ device.cpu }}<small>{{ device.memory }}</small><small v-if="device.host" class="mono">宿主机 {{ device.host }}</small></td><td><StatusBadge :status="device.status" :label="device.status === 'available' ? '空闲' : undefined" /></td><td v-if="!resourceEditingLocked"><button v-if="device.status === 'available'" class="text-button" :class="{ 'color-red': !pendingRemove.includes(device.id) }" @click="toggleRemoveDevice(device.id)">{{ pendingRemove.includes(device.id) ? '撤销摘除' : '摘除' }}</button><small v-else class="subtle-note">占用中</small></td></tr></tbody></table></div><div v-if="!resourceEditingLocked" class="adjust-devices"><select v-model="pendingAdd" aria-label="选择要加入的设备"><option disabled value="">选择要加入的设备（同架构/类型且空闲）</option><option v-for="candidate in adjustCandidates" :key="candidate.id" :value="candidate.id">{{ candidate.name }} · {{ candidate.id }}</option></select><button class="button secondary small" :disabled="!pendingAdd" @click="addPendingDevice">加入</button><button class="button primary small" :disabled="busy || !adjustDirty" @click="saveAdjustDevices">保存调整{{ adjustDirty ? `（+${pendingAddList.length} / -${pendingRemove.length}）` : '' }}</button></div><div v-else class="info-banner"><Icon name="lock" :size="18" /><p>占用期间不能调整关联；释放后可在此直接增减设备。</p></div><div v-if="utilizationData" class="info-banner"><Icon name="pulse" :size="18" /><p>近 {{ utilizationData.days }} 天利用率 <strong class="mono">{{ utilizationData.utilization }}%</strong> · 独占 {{ utilizationData.occupiedHours }} 小时（口径：租约占用时长 ÷ 可服务时长，与总览页固定样本不同源）</p></div><p v-else-if="utilizationNote" class="subtle-note">{{ utilizationNote }}</p><div v-if="selectedEnv.status === 'available' && !resourceEditingLocked" class="action-stack"><button class="button secondary" :disabled="busy" @click="runHealthCheck(true)"><Icon name="check" :size="16" />模拟健康检查通过</button><button class="button danger" :disabled="busy" @click="runHealthCheck(false)"><Icon name="alert" :size="16" />模拟检查异常并隔离</button></div><div v-if="selectedEnv.run || selectedEnv.lease" class="info-banner"><Icon name="lock" :size="18" /><p>当前占用：{{ selectedEnv.run?.id || selectedEnv.lease?.id }}。完成清理或健康复核前，设备保持锁定。</p></div><h3 class="section-title">拓扑关系</h3><svg class="topo-svg" :viewBox="`0 0 320 ${topoHeight}`" role="img" aria-label="环境与设备拓扑图"><rect x="8" :y="topoCenterY - 16" width="96" height="32" rx="9" class="topo-env" /><text x="56" :y="topoCenterY + 4" text-anchor="middle" class="topo-text">{{ selectedEnv.name.slice(0, 9) }}</text><template v-for="(device, i) in selectedEnv.devices" :key="device.id"><line x1="104" :y1="topoCenterY" :x2="168" :y2="i * 36 + 23" class="topo-line" /><rect x="168" :y="i * 36 + 10" width="136" height="26" rx="7" :class="['topo-device', { shared: sharedRefs(device).length }]" /><text x="236" :y="i * 36 + 27" text-anchor="middle" class="topo-text">{{ device.name }}<tspan v-if="sharedRefs(device).length" class="topo-shared">⇄ 共享</tspan></text></template></svg></template>
    <template v-else-if="modal.kind === 'borrow'">
      <form id="borrow-form" class="form-stack" @submit.prevent="submitBorrow">
        <EnvironmentPicker v-model="form.envIds" :envs="envs" :disabled="busy" />
        <label>借用时长<select v-model="form.hours" :disabled="busy"><option v-for="hours in [2,4,8,24,48]" :key="hours" :value="hours">{{ hours }} 小时</option></select></label>
        <label>用途说明<textarea v-model="form.purpose" required maxlength="200" rows="3" :disabled="busy" placeholder="例如：OS 24.03 RC2 网络协议回归验证" /><span class="field-hint">{{ form.purpose.length }} / 200</span></label>
        <div class="info-banner"><Icon name="shield" :size="18" /><p>空闲环境无需审批，提交即可借用。逐项检查所选环境，不可借的自动跳过并说明原因；其他环境照常借用。</p></div>
      </form>
    </template>
    <template v-else-if="modal.kind === 'borrowResult' && borrowResult">
      <div class="borrow-result-summary" :class="borrowResult.lease ? 'tone-green' : 'tone-amber'"><span class="soft-icon"><Icon :name="borrowResult.lease ? 'check' : 'info'" :size="24" /></span><div><h3>{{ borrowResult.lease ? '可用环境已直接借用' : '本次没有借到环境' }}</h3><p>成功 {{ borrowResult.outcomes.filter(item => item.status === 'borrowed').length }} 个 · 未借到 {{ borrowResult.outcomes.filter(item => item.status === 'skipped').length }} 个</p><small v-if="borrowResult.lease" class="mono">借用单 {{ borrowResult.lease.id }}</small></div></div>
      <div class="table-scroll"><table class="data-table"><thead><tr><th>环境</th><th>结果</th><th>说明</th></tr></thead><tbody><tr v-for="item in borrowResult.outcomes" :key="item.envId"><td>{{ item.name }}<small class="mono">{{ item.envId }}</small></td><td><span class="badge" :class="item.status === 'borrowed' ? 'tone-green' : 'tone-amber'">{{ item.status === 'borrowed' ? '借用成功' : '已跳过' }}</span></td><td class="wrap-cell">{{ item.reason }}</td></tr></tbody></table></div>
      <p class="page-footnote">{{ borrowResult.lease ? '借用单仅包含成功的环境；未借到的环境不会影响已成功借用的资源。' : '没有创建借用单或占用设备，可调整选择后重试。' }}</p>
    </template>
    <template v-else-if="['environmentEdit', 'deviceEdit'].includes(modal.kind)"><div v-if="resourceEditingLocked" class="info-banner"><Icon name="lock" :size="18" /><p>资源正在使用、维护或等待复核，当前可查看配置；释放后即可自助编辑。</p></div><ResourceEditor :kind="modal.kind === 'environmentEdit' ? 'environment' : 'device'" :entity="editedResource" :initial-draft="resourceEditorDraft" :devices="poolSnapshot.devices" :templates="modal.kind === 'environmentEdit' && !editedResource ? envTemplates : []" :busy="busy || resourceEditingLocked" :can-admin="isAdmin" @save="saveResource" @add-device="addDeviceForEnvironment" @save-template="saveTemplate" /></template>
    <template v-else-if="modal.kind === 'archiveResource' && archiveTarget"><div class="notice-banner"><Icon name="box" :size="24" /><div><strong>{{ archiveTarget.name }}</strong><p>移出后不再展示于可借用资源池，已有借用、维护与执行历史会保留。</p></div></div><p class="subtle-note">正在使用的设备不能移出。设备仍被其他环境引用时，请先调整关联。</p></template>
    <template v-else-if="modal.kind === 'lease' && selectedLease"><div class="detail-summary"><div><strong class="mono">{{ selectedLease.id }}</strong><p>{{ scopeName(selectedLease) }}</p></div><StatusBadge :status="selectedLease.status" /></div><div class="info-banner lease-scope-banner"><Icon name="box" :size="18" /><p>本单包含 {{ environmentIdsFor(selectedLease).length }} 个环境、{{ deviceIdsFor(selectedLease, snapshot).length }} 台设备，整单统一续期、归还与健康复核。</p></div><dl class="detail-grid"><div><dt>借用人</dt><dd>{{ selectedLease.user }}</dd></div><div><dt>借用用途</dt><dd>{{ selectedLease.purpose }}</dd></div><div><dt>借用时间</dt><dd>{{ date(selectedLease.start) }}</dd></div><div><dt>到期时间</dt><dd>{{ date(selectedLease.end) }}</dd></div></dl><div class="info-banner"><Icon name="shield" :size="18" /><p>{{ selectedLease.status === 'checking' ? '归还已提交，设备保持锁定。请模拟健康检查结果，确认后再释放或隔离。' : '归还后需进行健康复核；借用到期也不会自动释放设备。' }}</p></div><div v-if="selectedLease.status === 'active'" class="action-stack"><button class="button secondary" :disabled="busy" @click="action(() => api.renew(selectedLease.id), '借用已续期 4 小时。', false)"><Icon name="clock" :size="17" />续期 4 小时</button><button class="button primary" :disabled="busy" @click="action(() => api.requestReturn(selectedLease.id), '已提交归还，等待健康复核。', false)">提交归还检查</button></div><div v-else-if="selectedLease.status === 'checking'" class="action-stack"><button class="button danger" :disabled="busy" @click="action(() => api.verifyReturn(selectedLease.id, false), '检查异常，设备已隔离。', false)">模拟检查异常并隔离</button><button class="button primary" :disabled="busy" @click="action(() => api.verifyReturn(selectedLease.id, true), '健康检查通过，设备已释放。', false)">模拟检查通过并释放</button></div></template>
    <template v-else-if="modal.kind === 'maintenance'"><form id="maintenance-form" class="form-stack" @submit.prevent="submitMaintenance"><label>维护环境<select v-model="form.envId" required><option disabled value="">请选择空闲环境</option><option v-for="env in availableEnvs" :key="env.id" :value="env.id">{{ env.name }}</option></select></label><label>维护原因<textarea v-model="form.reason" rows="4" required maxlength="200" placeholder="填写维护原因、计划操作与复核要求" /></label><div class="info-banner"><Icon name="lock" :size="18" /><p>只能对空闲环境开始维护；共享设备的关联环境会同步标为不可借。</p></div></form></template>
    <template v-else-if="modal.kind === 'maintenanceDetail' && selectedMaintenance"><div class="detail-summary"><div><strong class="mono">{{ selectedMaintenance.id }}</strong><p>{{ scopeName(selectedMaintenance) }}</p></div><StatusBadge :status="selectedMaintenance.status" /></div><dl class="detail-grid"><div><dt>处理原因</dt><dd>{{ selectedMaintenance.reason }}</dd></div><div><dt>关联问题</dt><dd class="mono">{{ selectedMaintenance.issue }}</dd></div><div><dt>负责人</dt><dd>{{ selectedMaintenance.user }}</dd></div><div><dt>创建时间</dt><dd>{{ date(selectedMaintenance.date) }}</dd></div></dl><div class="info-banner"><Icon name="shield" :size="18" /><p>以下按钮模拟维护完成后的健康复核，只有通过后环境才恢复可借用。</p></div><div v-if="selectedMaintenance.status !== 'success'" class="action-stack"><button class="button danger" :disabled="busy" @click="action(() => api.verifyMaintenance(selectedMaintenance.id, false), '复核异常，设备保持隔离。')">模拟复核异常</button><button class="button primary" :disabled="busy" @click="action(() => api.verifyMaintenance(selectedMaintenance.id, true), '复核通过，关联环境状态已更新。')">模拟复核通过</button></div></template>
    <template v-else-if="modal.kind === 'run' && selectedRun"><div class="detail-summary"><div><strong class="mono">{{ selectedRun.id }}</strong><p>{{ selectedRun.name }}</p></div><StatusBadge :status="selectedRun.status" /></div><div class="run-progress"><div class="progress-track"><i :style="{ width: selectedRun.progress + '%' }" /></div><strong class="mono">{{ selectedRun.progress }}%</strong></div><dl class="detail-grid"><div><dt>关联环境</dt><dd><button class="inline-link" @click="details(envs.find(e => e.id === selectedRun.envId))">{{ selectedRun.envId }}</button></dd></div><div><dt>版本</dt><dd>{{ selectedRun.version }}</dd></div><div><dt>配置快照</dt><dd class="mono">{{ selectedRun.config }}</dd></div><div><dt>Jenkins 构建标识</dt><dd class="mono">{{ selectedRun.build }} · 示例</dd></div><div><dt>制品引用</dt><dd class="mono break-word">{{ selectedRun.artifact }}</dd></div><div><dt>执行人</dt><dd>{{ selectedRun.user }}</dd></div></dl><h3 class="section-title">执行与证据链</h3><ol class="evidence-list"><li v-for="(label, i) in ['配置及参数快照', '环境与设备独占记录', '构建及制品关联', '测试结果与日志', '清理与释放确认']" :key="label"><span :class="['evidence-step', { done: i < selectedRun.evidence }]"><Icon :name="i < selectedRun.evidence ? 'check' : 'clock'" :size="15" /></span><div><strong>{{ label }}</strong><small>{{ i < selectedRun.evidence ? '已记录 · 演示证据' : '等待当前阶段确认' }}</small></div></li></ol><pre class="log-preview">[mock] run_id={{ selectedRun.id }}
[mock] environment={{ selectedRun.envId }}
[mock] state={{ selectedRun.status }}
[mock] 此处为演示日志，未连接 Jenkins 或真实执行端。</pre><div v-if="['running','reconciling','cleaning'].includes(selectedRun.status)" class="info-banner"><Icon name="info" :size="18" /><p>{{ selectedRun.status === 'reconciling' ? '状态不明，必须对账确认任务终态，再进入清理。' : selectedRun.status === 'cleaning' ? '任务已结束，设备保持锁定，清理通过后才能释放。' : '以下操作模拟执行终态，随后仍需确认设备清理。' }}</p></div><div class="action-stack" v-if="selectedRun.status === 'running'"><button class="button secondary" :disabled="busy" @click="action(() => api.finishRun(selectedRun.id, false), '已记录失败终态，设备待清理。', false)">模拟执行失败</button><button class="button primary" :disabled="busy" @click="action(() => api.finishRun(selectedRun.id, true), '已记录成功终态，设备待清理。', false)">模拟执行成功</button></div><button v-else-if="selectedRun.status === 'reconciling'" class="button primary full-width" :disabled="busy" @click="action(() => api.finishRun(selectedRun.id, true), '已确认任务终态，等待清理。', false)">模拟对账：确认执行已结束</button><div v-else-if="selectedRun.status === 'cleaning'" class="action-stack"><button class="button danger" :disabled="busy" @click="action(() => api.cleanupRun(selectedRun.id, false), '清理异常，设备已隔离。', false)">模拟清理异常</button><button class="button primary" :disabled="busy" @click="action(() => api.cleanupRun(selectedRun.id, true), '清理通过，设备已释放。', false)">确认清理并释放</button></div></template>
    <template v-else-if="modal.kind === 'pipeline' && selectedPipeline"><div class="detail-summary"><div><strong>{{ selectedPipeline.name }}</strong><p class="mono">{{ selectedPipeline.id }} @ {{ selectedPipeline.version }}</p></div><StatusBadge :status="selectedPipeline.status" /></div><pre v-if="configXml" class="xml-preview">{{ configXml }}</pre><p v-else class="subtle-note">该配置暂无 XML 内容。</p><form v-if="selectedPipeline.status === 'published'" id="pipeline-form" class="form-stack" @submit.prevent="startPipeline"><label>选择匹配的空闲环境<select v-model="form.envId" required><option disabled value="">请选择 {{ selectedPipeline.arch }} 环境</option><option v-for="env in pipelineEnvs" :key="env.id" :value="env.id">{{ env.name }} · {{ env.id }}</option></select></label><p v-if="!pipelineEnvs.length" class="subtle-note">暂无匹配架构的可用环境。</p></form><div class="info-banner"><Icon name="info" :size="18" /><p>{{ selectedPipeline.status === 'draft' ? '草稿不能启动任务；当前版本提供只读配置预览，发布治理为后续独立设计。' : '启动仅在本地创建演示任务并独占环境设备，不调用 Jenkins；XML 为只读配置内容预览。' }}</p></div></template>
    <template v-else-if="modal.kind === 'info'"><div class="table-scroll"><table class="data-table metric-definitions"><thead><tr><th>指标</th><th>定义与范围</th></tr></thead><tbody><tr><td>测试通过率</td><td>所选版本及周期内通过用例执行数 ÷ 有结果的用例执行总数；同一用例多次执行分别计数。</td></tr><tr><td>执行任务</td><td>所选版本及周期内的运行次数；与用例执行数量分开统计。</td></tr><tr><td>环境利用率</td><td>所选版本 7 天固定样本的独占占用设备时长 ÷ 可服务设备时长；不随 3 天选项变化。</td></tr><tr><td>准备耗时 P50</td><td>所选版本 7 天固定样本中，从分配设备到环境就绪的中位耗时。</td></tr><tr><td>证据完整率</td><td>所选版本 7 天固定样本中，同时具备五项证据的运行占比。</td></tr><tr><td>环境资源</td><td>当前本地资源快照，随借用、维护和执行操作变化；不与历史版本统计混用。</td></tr><tr><td>待回归问题</td><td>所选版本的固定问题快照，不随统计周期或本地资源变化。</td></tr><tr><td>版本验证链路</td><td>展示目标系统与关联方式的固定样例，尚未接入 Gerrit、Jenkins、JFrog、RDC。</td></tr></tbody></table></div><div class="info-banner"><Icon name="info" :size="18" /><p>历史窗口固定截至 2026-10-30；mock 操作只改变当前资源快照。未展示部分样本明细，所有数据仅用于前端评审。</p></div></template>
    <template v-else-if="modal.kind === 'settings'"><div class="settings-section"><h3>外观主题</h3><p>主题偏好保存在本浏览器，切换时保留页面与筛选状态。</p><div class="theme-cards"><button v-for="item in themeOptions" :key="item.id" :class="{ active: preference === item.id }" :aria-pressed="preference === item.id" @click="preference = item.id"><Icon :name="item.icon" :size="24" /><strong>{{ item.label }}</strong><span>{{ item.id === 'system' ? '跟随系统外观' : item.id === 'light' ? '清晰明亮的工作空间' : '专注深色数据视图' }}</span></button></div><p class="subtle-note">当前外观：{{ resolved === 'light' ? '浅色' : '深色' }}</p></div><div class="settings-section"><h3>演示数据</h3><p>借用、维护和任务变化保存在本浏览器。可恢复初始样本重新体验；此操作只清除本平台的 mock 数据，属于删除类危险操作。</p><button v-if="!resetConfirm" class="button secondary" :disabled="!isAdmin" :title="isAdmin ? undefined : '需要管理员权限'" @click="resetConfirm = true"><Icon name="refresh" :size="17" />恢复初始演示数据</button><p v-if="!isAdmin" class="subtle-note">普通人员不能重置演示数据，请切换为管理员身份。</p><div v-else-if="resetConfirm" class="action-stack"><button class="button secondary" @click="resetConfirm = false">保留当前数据</button><button class="button danger" :disabled="busy" @click="action(() => api.reset(), '已恢复初始演示数据。')">确认重置演示数据</button></div></div></template>
    <template v-else-if="modal.kind === 'issue' && selectedIssue"><div class="detail-summary"><div><strong class="mono">{{ selectedIssue.id }}</strong><p>{{ selectedIssue.title }}</p></div><StatusBadge :status="selectedIssue.status" /></div><dl class="detail-grid"><div><dt>负责人</dt><dd>{{ selectedIssue.owner }}</dd></div><div><dt>优先级</dt><dd>{{ selectedIssue.severity }}</dd></div><div><dt>所属模块</dt><dd>{{ selectedIssue.module }}</dd></div><div><dt>关联版本</dt><dd>OS 24.03 RC2</dd></div></dl><div class="info-banner"><Icon name="link" :size="18" /><p>此处演示问题的追溯信息；RDC 尚未接入，当前不执行真实问题流转。</p></div></template>
    <template v-else-if="modal.kind === 'identity'"><div class="notice-banner"><Icon name="user" :size="22" /><div><strong>演示身份切换</strong><p>切换后以所选身份执行操作，用于验证权限拦截；只影响本浏览器的演示数据。</p></div></div><div class="identity-list"><button v-for="user in snapshot.users.filter(u => u.status === 'active')" :key="user.id" :class="['identity-item', { current: user.id === currentUser?.id }]" @click="user.id === currentUser?.id || switchUser(user)"><span class="avatar">{{ user.name.slice(0, 1) }}</span><div><strong>{{ user.name }}</strong><small>{{ user.title }}</small></div><span class="badge" :class="user.role === 'admin' ? 'tone-amber' : 'tone-blue'">{{ roleLabel(user) }}</span><Icon v-if="user.id === currentUser?.id" name="check" :size="17" /></button></div><p class="page-footnote">{{ isAdmin ? '停用人员不出现在此列表；如需调整角色或停用状态，前往人员管理页。' : '停用人员不出现在此列表。' }}</p></template>
    <template v-else-if="modal.kind === 'userAdd'"><form id="user-add-form" class="form-stack" @submit.prevent="submitUserAdd"><label>人员姓名<input v-model="userForm.name" required maxlength="20" :disabled="busy" placeholder="例如：赵倩" /></label><label>初始角色<select v-model="userForm.role" :disabled="busy"><option value="member">普通人员（新增资源与删除类危险操作受限）</option><option value="admin">管理员（全部权限）</option></select></label><div class="info-banner"><Icon name="shield" :size="18" /><p>权限模型从简：管理员拥有全部权限；普通人员不能新增环境 / 设备，删除类危险操作（移出资源池、重置演示数据）与人员管理修改同样受限。</p></div></form></template>
    <template v-else-if="modal.kind === 'restoreResource' && restoreTarget"><div class="notice-banner"><Icon name="refresh" :size="24" /><div><strong>{{ restoreTarget.name }}</strong><p>恢复前会重新校验：关联设备仍在池内、架构/类型一致且未被占用，全部满足才恢复成功。</p></div></div><p class="subtle-note">恢复后环境立即回到可借用资源池；历史借用与执行记录保持不变。</p></template>
    <template v-else-if="modal.kind === 'timeline' && timelineEnv"><div class="detail-summary"><div class="resource-icon"><Icon name="box" :size="30" /></div><div><span class="mono">{{ timelineEnv.id }}</span><p>{{ timelineEnv.name }} 的借用、执行、维护与关键事件（{{ timelineItems.length }} 条）</p></div><StatusBadge :status="timelineEnv.lifecycle === 'retired' ? 'released' : timelineEnv.status" /></div><ol v-if="timelineItems.length" class="timeline-list"><li v-for="(item, i) in timelineItems" :key="i"><i class="dot" :class="item.tone" /><div><strong>{{ item.kind }} · <span class="mono">{{ item.ref }}</span></strong><small>{{ item.detail }}</small></div><time class="mono">{{ (item.time || '').slice(0, 16).replace('T', ' ') }}</time></li></ol><div v-else class="empty-state"><Icon name="clipboard" :size="32" /><p>该环境暂无历史记录</p></div></template>
    <template v-else-if="modal.kind === 'eventsStream'"><div v-if="flowCounts" class="info-banner"><Icon name="pulse" :size="18" /><p>近 7 天流转：借用 {{ flowCounts.batch_borrow_result || 0 }} 次 · 维护开始 {{ flowCounts.maintenance_start || 0 }} · 移出 {{ flowCounts.environment_retire || 0 }} · 恢复 {{ flowCounts.environment_restore || 0 }} · 健康检查 {{ flowCounts.environment_health_check || 0 }}</p></div><ol v-if="eventsData.length" class="timeline-list"><li v-for="event in eventsData" :key="event.id"><i class="dot" :class="event.tone" /><div><strong>{{ event.text || event.action }}</strong><small>{{ event.actor }} · {{ event.entityType }} <span class="mono">{{ event.entityId }}</span></small></div><time class="mono">{{ event.occurredAt.slice(5, 16).replace('T', ' ') }}</time></li></ol><div v-else-if="eventsLoading" class="loading-state" role="status"><span class="spinner" /><p>正在加载事件…</p></div><div v-else class="empty-state"><Icon name="list" :size="32" /><p>暂无事件记录</p><p class="subtle-note">浏览器 Mock 模式仅有最近动态；连接后端后此处展示完整审计事件流。</p></div></template>
    <template v-else-if="modal.kind === 'batchResult' && batchOutcomes.length"><div class="borrow-result-summary" :class="batchOutcomes.every(i => i.status === 'done') ? 'tone-green' : 'tone-amber'"><span class="soft-icon"><Icon :name="batchOutcomes.every(i => i.status === 'done') ? 'check' : 'info'" :size="24" /></span><div><h3>{{ batchTarget === 'retire' ? '批量移出完成' : '批量恢复完成' }}</h3><p>成功 {{ batchOutcomes.filter(i => i.status === 'done').length }} 个 · 失败 {{ batchOutcomes.filter(i => i.status !== 'done').length }} 个</p></div></div><div class="table-scroll"><table class="data-table"><thead><tr><th>环境</th><th>结果</th><th>说明</th></tr></thead><tbody><tr v-for="item in batchOutcomes" :key="item.envId"><td>{{ item.name }}<small class="mono">{{ item.envId }}</small></td><td><span class="badge" :class="item.status === 'done' ? 'tone-green' : 'tone-amber'">{{ item.status === 'done' ? '成功' : '失败' }}</span></td><td class="wrap-cell">{{ item.reason }}</td></tr></tbody></table></div><p class="page-footnote">批量操作逐项执行，单项失败不影响其他环境；沿用部分成功语义。</p></template>
    <template #footer><span class="subtle-note"><Icon v-if="busy" name="refresh" :size="14" :class="{ spinning: busy }" />{{ busy ? '正在处理…' : '仅修改本地演示数据' }}</span><div class="modal-footer-actions"><button class="button secondary" :disabled="busy" @click="closeModal">{{ ['borrow','maintenance','environmentEdit','deviceEdit','archiveResource'].includes(modal.kind) ? '取消' : '关闭' }}</button><template v-if="modal.kind === 'environment' && selectedEnv"><button v-if="isAdmin" class="button secondary" @click="copyEnvironment(selectedEnv)">复制环境</button><button class="button secondary" @click="editResource('environment', selectedEnv)">编辑环境</button><button class="button danger" :disabled="!isAdmin" :title="isAdmin ? undefined : '需要管理员权限'" @click="confirmArchive('environment', selectedEnv)">移出资源池</button><button v-if="selectedEnv.status === 'available'" class="button primary" @click="borrow(selectedEnv)">申请借用</button><button v-else-if="selectedEnv.run" class="button primary" @click="runDetails(selectedEnv.run)">查看执行</button><button v-else-if="selectedEnv.lease" class="button primary" @click="leaseDetails(selectedEnv.lease)">管理借用</button></template><button v-if="modal.kind === 'borrow'" class="button primary" type="submit" form="borrow-form" :disabled="busy || borrowSelectionInvalid || !form.purpose?.trim()">确认借用 {{ form.envIds.length || '' }}{{ form.envIds.length ? ' 个环境' : '' }}</button><template v-if="modal.kind === 'borrowResult' && borrowResult"><button v-if="borrowResult.outcomes.some(item => item.status === 'skipped')" class="button secondary" @click="retrySkipped">调整未借到的环境</button><button v-if="borrowResult.lease" class="button primary" @click="leaseDetails(borrowResult.lease)">管理成功借用</button></template><button v-if="['environmentEdit','deviceEdit'].includes(modal.kind)" class="button primary" type="submit" form="resource-form" :disabled="busy || resourceEditingLocked">保存资源</button><button v-if="modal.kind === 'archiveResource'" class="button danger" :disabled="busy" @click="archiveResource">确认移出</button><button v-if="modal.kind === 'restoreResource'" class="button primary" :disabled="busy" @click="doRestore">确认恢复</button><button v-if="modal.kind === 'maintenance'" class="button primary" type="submit" form="maintenance-form" :disabled="busy || !form.envId || !form.reason?.trim()">开始维护</button><button v-if="modal.kind === 'pipeline' && selectedPipeline.status === 'published'" class="button primary" type="submit" form="pipeline-form" :disabled="busy || !form.envId">启动演示任务</button><button v-if="modal.kind === 'userAdd'" class="button primary" type="submit" form="user-add-form" :disabled="busy || !userForm.name?.trim()">确认新增</button></div></template>
  </ModalShell>
</template>
