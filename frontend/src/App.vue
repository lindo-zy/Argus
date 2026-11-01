<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import Icon from './components/Icon.vue'
import StatusBadge from './components/StatusBadge.vue'
import ModalShell from './components/ModalShell.vue'
import EnvironmentPicker from './components/EnvironmentPicker.vue'
import ResourceEditor from './components/ResourceEditor.vue'
import EnvironmentsView from './views/EnvironmentsView.vue'
import DashboardView from './views/DashboardView.vue'
import WorkspaceView from './views/WorkspaceView.vue'
import { useTheme } from './composables/useTheme.js'
import { createMockApi } from './mock/api.js'
import { createSeed, enrichEnvironments, environmentIdsFor, deviceIdsFor } from './mock/seed.js'

const navGroups = [{ label: '总览', items: [{ id: 'dashboard', label: '总览', icon: 'home' }] }, { label: '资源管理', items: [{ id: 'environments', label: '环境资源', icon: 'box' }, { id: 'devices', label: '设备资产', icon: 'monitor' }, { id: 'leases', label: '借用管理', icon: 'lock' }, { id: 'maintenance', label: '维护中心', icon: 'shield' }] }, { label: '测试协同', items: [{ id: 'pipelines', label: '流水线配置', icon: 'flow' }, { id: 'runs', label: '执行记录', icon: 'clipboard' }, { id: 'quality', label: '质量分析', icon: 'chart' }] }, { label: '平台管理', items: [{ id: 'people', label: '人员管理', icon: 'user', adminOnly: true }] }]
const navigation = navGroups.flatMap(g => g.items)
const getPage = () => navigation.some(n => n.id === location.hash.slice(2)) ? location.hash.slice(2) : 'environments'
const page = ref(getPage()), mobileOpen = ref(false)
const currentTitle = computed(() => navigation.find(n => n.id === page.value)?.label)
const currentGroup = computed(() => navGroups.find(g => g.items.some(n => n.id === page.value))?.label)
const { preference, resolved, presentation } = useTheme()
const themeOptions = [{ id: 'light', label: '浅色', icon: 'sun' }, { id: 'dark', label: '深色', icon: 'moon' }, { id: 'system', label: '系统', icon: 'monitor' }]
let storage
try { storage = window.localStorage } catch { storage = null }
const api = createMockApi({ storage })
const snapshot = ref(createSeed()), loading = ref(true), loadError = ref(''), busy = ref(false), modalError = ref('')
const envs = computed(() => enrichEnvironments(snapshot.value))
const poolSnapshot = computed(() => ({ ...snapshot.value, devices: snapshot.value.devices.filter(d => d.lifecycle !== 'retired') }))
const currentUser = computed(() => snapshot.value.users?.find(u => u.id === snapshot.value.currentUserId) || null)
const isAdmin = computed(() => currentUser.value?.role === 'admin')
const visibleNavGroups = computed(() => navGroups.map(g => ({ ...g, items: g.items.filter(item => !item.adminOnly || isAdmin.value) })).filter(g => g.items.length))
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
  loading.value = true; loadError.value = ''
  try { const result = await api.load(); snapshot.value = result.snapshot; if (result.persistenceWarning) notify('本地存储不可用或数据已损坏，当前使用内存演示数据。', 'amber') }
  catch (error) { loadError.value = error.message || '数据加载失败，请重试。' }
  finally { loading.value = false }
}
async function action(operation, message, close = true) {
  if (busy.value) return
  busy.value = true; modalError.value = ''
  try { const result = await operation(); snapshot.value = result.snapshot; if (close) modal.value = null; notify(message); return result }
  catch (error) { modalError.value = error.message || '操作未完成，请重试。' }
  finally { busy.value = false }
}
function navigate(id) { page.value = id; location.hash = `/${id}`; mobileOpen.value = false; searchOpen.value = false; modal.value = null }
function routeChange() { page.value = getPage(); mobileOpen.value = false; presentation.value = false; window.scrollTo({ top: 0 }) }
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
  resourceEditorDraft.value = null; environmentReturnDraft.value = null; open(kind === 'device' ? 'deviceEdit' : 'environmentEdit', resource?.id)
}
function addDeviceForEnvironment(draft) {
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
const configXml = computed(() => selectedPipeline.value ? `<pipeline id="${selectedPipeline.value.id}" version="${selectedPipeline.value.version}">\n  <jenkins connection="jenkins-main" job="${selectedPipeline.value.job}" />\n  <environment architecture="${selectedPipeline.value.arch}">\n    <occupancy mode="exclusive" />\n  </environment>\n  <parameters>\n    <parameter name="IMAGE_REF" type="string" required="true" />\n    <parameter name="TEST_SUITE" type="enum" default="smoke" />\n  </parameters>\n  <execution timeoutMinutes="120" />\n</pipeline>` : '')
const modalTitle = computed(() => ({ environment: selectedEnv.value?.name, borrow: '申请借用环境', lease: '借用记录', maintenance: '创建维护', maintenanceDetail: '维护与健康复核', run: '执行详情', pipeline: '流水线配置', info: '指标口径与数据范围', settings: '工作空间设置', notifications: '通知与待办', issue: '问题追溯', borrowResult: '环境借用结果', environmentEdit: editedResource.value ? '编辑环境' : '新增环境', deviceEdit: editedResource.value ? '编辑设备' : '新增设备', archiveResource: '移出资源池', identity: '演示身份切换', userAdd: '新增人员' })[modal.value?.kind] || '')
onMounted(() => { load(); window.addEventListener('hashchange', routeChange); window.addEventListener('keydown', keydown) })
onBeforeUnmount(() => { clearTimeout(toastTimer); window.removeEventListener('hashchange', routeChange); window.removeEventListener('keydown', keydown) })
</script>

<template>
  <div class="app-shell" :class="{ 'presentation-mode': presentation, 'mobile-open': mobileOpen }">
    <a href="#main-content" class="skip-link" @click.prevent="focusMain">跳到主要内容</a>
    <button v-if="mobileOpen" class="sidebar-backdrop" aria-label="收起导航" @click="mobileOpen = false" />
    <aside class="sidebar" aria-label="主导航"><a class="brand" href="#/environments"><svg viewBox="0 0 48 48" aria-hidden="true"><path d="M24 3 43 14v20L24 45 5 34V14Z" fill="none" stroke="currentColor" stroke-width="3" /><path d="M11 24s5-9 13-9 13 9 13 9-5 9-13 9-13-9-13-9Z" fill="currentColor" opacity=".2" /><circle cx="24" cy="24" r="5" fill="currentColor" /></svg><div><strong>ARGUS</strong><span>操作系统测试平台</span></div></a>
      <nav><div class="nav-group" v-for="group in visibleNavGroups" :key="group.label"><div class="nav-label">{{ group.label }}</div><a v-for="item in group.items" :key="item.id" :href="`#/${item.id}`" :class="['nav-item', { active: page === item.id }]" :aria-current="page === item.id ? 'page' : undefined" :title="item.label" @click="mobileOpen = false"><Icon :name="item.icon" :size="21" /><span>{{ item.label }}</span><i v-if="item.id === 'maintenance'" class="nav-notice" /></a></div></nav>
      <div class="sidebar-bottom"><div class="workspace-chip"><span class="workspace-symbol">OS</span><div><strong>系统测试</strong><small>研发部 · 演示工作空间</small></div><Icon name="down" :size="15" /></div><button class="nav-item settings-button" aria-label="工作空间设置" @click="open('settings')"><Icon name="settings" :size="21" /><span>工作空间设置</span></button><div class="sidebar-version"><i class="dot teal" />LOCAL MOCK<span>v0.1.0</span></div></div>
    </aside>
    <div class="app-main"><header class="topbar"><div class="breadcrumb"><button class="icon-button mobile-menu" aria-label="打开导航" :aria-expanded="mobileOpen" @click="mobileOpen = !mobileOpen"><Icon name="menu" /></button><Icon name="home" :size="17" /><span>{{ currentGroup }}</span><span class="breadcrumb-slash">/</span><strong>{{ currentTitle }}</strong></div>
      <div class="topbar-tools"><div class="global-search" @focusout="event => { if (!event.currentTarget.contains(event.relatedTarget)) searchOpen = false }"><label class="search-field"><Icon name="search" :size="17" /><input ref="searchInput" v-model="searchQuery" aria-label="全局搜索" placeholder="搜索环境、设备或任务…" @focus="searchOpen = true" @keydown.enter.prevent="searchResults[0] && pickSearch(searchResults[0])" /><kbd>⌘ K</kbd></label><div v-if="searchOpen && searchQuery.trim()" class="search-popover"><div class="search-popover-label">搜索结果</div><button v-for="result in searchResults" :key="result.id" @click="pickSearch(result)"><Icon :name="result.icon" :size="18" /><span><strong>{{ result.name }}</strong><small class="mono">{{ result.id }}</small></span><span class="subtle-note">{{ result.label }}</span></button><div v-if="!searchResults.length" class="search-no-result">未找到匹配的环境、设备或任务</div></div></div>
        <div class="theme-toggle" role="group" aria-label="主题切换"><button v-for="item in themeOptions" :key="item.id" :class="{ active: preference === item.id }" :aria-pressed="preference === item.id" :title="item.id === 'system' ? '跟随系统' : `${item.label}主题`" @click="preference = item.id"><Icon :name="item.icon" :size="16" /><span>{{ item.label }}</span></button></div><button class="icon-button notification-button" aria-label="通知与待办" @click="open('notifications')"><Icon name="bell" :size="22" /><i /></button><button class="user-button" aria-label="演示身份切换" @click="open('identity')"><span class="avatar">{{ currentUser?.name?.slice(0, 1) || '?' }}</span><span>{{ currentUser?.name }}</span><span class="badge" :class="isAdmin ? 'tone-amber' : 'tone-blue'">{{ roleLabel(currentUser) }}</span><Icon name="down" :size="13" /></button></div>
    </header>
    <main id="main-content" class="page-content" tabindex="-1"><div v-if="loading" class="loading-state" role="status"><span class="spinner" /><h2>正在准备工作空间</h2><p>加载环境与执行数据…</p></div><div v-else-if="loadError" class="empty-state panel" role="alert"><Icon name="alert" :size="36" /><h2>暂时无法加载数据</h2><p>{{ loadError }}</p><button class="button primary" @click="load">重新加载</button></div><template v-else>
      <div v-show="page === 'environments'"><EnvironmentsView :envs="envs" :snapshot="poolSnapshot" @details="details" @borrow="borrow" @run="runDetails" @lease="leaseDetails" @navigate="navigate" @maintenance="maintenance" @create="editResource('environment')" /></div>
      <div v-show="page === 'dashboard'"><DashboardView :envs="envs" :snapshot="poolSnapshot" :presentation="presentation" @navigate="navigate" @run="runDetails" @info="open('info')" @presentation="togglePresentation" /></div>
      <WorkspaceView v-if="!['environments', 'dashboard'].includes(page)" :page="page" :envs="envs" :snapshot="poolSnapshot" :current-user="currentUser" :can-admin="isAdmin" @details="details" @run="runDetails" @lease="leaseDetails" @borrow="borrow" @maintenance="maintenance" @pipeline="pipeline" @issue="issue => open('issue', issue.id)" @device-create="editResource('device')" @device-edit="device => editResource('device', device)" @device-remove="device => confirmArchive('device', device)" @user-add="open('userAdd')" @user-role="(user, role) => action(() => api.setUserRole(user.id, role), `已将 ${user.name} 设为${roleLabel({ role })}。`)" @user-status="(user, status) => action(() => api.setUserStatus(user.id, status), `已${status === 'disabled' ? '停用' : '启用'} ${user.name}。`)" @user-switch="switchUser" />
      <footer class="app-footer"><span><i class="dot teal" />ARGUS · 本地演示工作空间</span><span>演示数据 · 未接入真实设备与外部系统</span><button class="text-button" @click="load"><Icon name="refresh" :size="13" />刷新数据</button></footer>
    </template></main></div>
  </div>

  <Transition name="toast"><div v-if="toast" class="toast-message" :class="`tone-${toast.tone}`" role="status"><Icon :name="toast.tone === 'green' ? 'check' : 'info'" :size="19" /><span>{{ toast.message }}</span><button class="icon-button" aria-label="关闭提示" @click="toast = null"><Icon name="close" :size="16" /></button></div></Transition>

  <ModalShell v-if="modal" :key="modal.kind" :title="modalTitle" :subtitle="modal.kind === 'info' ? '所有数值均为演示样本，不代表生产结果。' : '本地 MOCK · 所有操作只作用于演示数据'" :wide="['environment', 'borrow', 'borrowResult', 'environmentEdit', 'deviceEdit', 'run', 'pipeline', 'info'].includes(modal.kind)" :drawer="['environment', 'run'].includes(modal.kind)" @close="closeModal">
    <p v-if="modalError" class="form-error" role="alert"><Icon name="alert" :size="17" />{{ modalError }}</p>
    <template v-if="modal.kind === 'environment' && selectedEnv"><div class="detail-summary"><div class="resource-icon"><Icon name="server" :size="30" /></div><div><span class="mono">{{ selectedEnv.id }}</span><p>{{ selectedEnv.description }}</p></div><StatusBadge :status="selectedEnv.status" /></div><dl class="detail-grid"><div><dt>处理器架构</dt><dd>{{ selectedEnv.arch }}</dd></div><div><dt>环境类型</dt><dd>{{ selectedEnv.type === 'physical' ? '物理机' : '虚拟机' }}</dd></div><div><dt>操作系统</dt><dd>{{ selectedEnv.version || '未记录' }}</dd></div><div><dt>设备数量</dt><dd>{{ selectedEnv.deviceIds.length }} 台</dd></div><div><dt>部署位置</dt><dd>{{ selectedEnv.location }}</dd></div><div><dt>最近健康检查</dt><dd class="mono">{{ selectedEnv.health }}</dd></div></dl><div v-if="selectedEnv.shared" class="info-banner"><Icon name="link" :size="18" /><p>此环境包含共享设备；任一关联设备被占用，所有引用环境都将同步锁定。</p></div><h3 class="section-title">关联设备</h3><div class="table-scroll"><table class="data-table"><thead><tr><th>设备</th><th>规格</th><th>状态</th></tr></thead><tbody><tr v-for="device in selectedEnv.devices" :key="device.id"><td class="mono">{{ device.id }}<small>{{ device.name }}</small></td><td>{{ device.cpu }}<small>{{ device.memory }}</small></td><td><StatusBadge :status="device.status" :label="device.status === 'available' ? '空闲' : undefined" /></td></tr></tbody></table></div><div v-if="selectedEnv.run || selectedEnv.lease" class="info-banner"><Icon name="lock" :size="18" /><p>当前占用：{{ selectedEnv.run?.id || selectedEnv.lease?.id }}。完成清理或健康复核前，设备保持锁定。</p></div></template>
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
    <template v-else-if="['environmentEdit', 'deviceEdit'].includes(modal.kind)"><div v-if="resourceEditingLocked" class="info-banner"><Icon name="lock" :size="18" /><p>资源正在使用、维护或等待复核，当前可查看配置；释放后即可自助编辑。</p></div><ResourceEditor :kind="modal.kind === 'environmentEdit' ? 'environment' : 'device'" :entity="editedResource" :initial-draft="resourceEditorDraft" :devices="poolSnapshot.devices" :busy="busy || resourceEditingLocked" @save="saveResource" @add-device="addDeviceForEnvironment" /></template>
    <template v-else-if="modal.kind === 'archiveResource' && archiveTarget"><div class="notice-banner"><Icon name="box" :size="24" /><div><strong>{{ archiveTarget.name }}</strong><p>移出后不再展示于可借用资源池，已有借用、维护与执行历史会保留。</p></div></div><p class="subtle-note">正在使用的设备不能移出。设备仍被其他环境引用时，请先调整关联。</p></template>
    <template v-else-if="modal.kind === 'lease' && selectedLease"><div class="detail-summary"><div><strong class="mono">{{ selectedLease.id }}</strong><p>{{ scopeName(selectedLease) }}</p></div><StatusBadge :status="selectedLease.status" /></div><div class="info-banner lease-scope-banner"><Icon name="box" :size="18" /><p>本单包含 {{ environmentIdsFor(selectedLease).length }} 个环境、{{ deviceIdsFor(selectedLease, snapshot).length }} 台设备，整单统一续期、归还与健康复核。</p></div><dl class="detail-grid"><div><dt>借用人</dt><dd>{{ selectedLease.user }}</dd></div><div><dt>借用用途</dt><dd>{{ selectedLease.purpose }}</dd></div><div><dt>借用时间</dt><dd>{{ date(selectedLease.start) }}</dd></div><div><dt>到期时间</dt><dd>{{ date(selectedLease.end) }}</dd></div></dl><div class="info-banner"><Icon name="shield" :size="18" /><p>{{ selectedLease.status === 'checking' ? '归还已提交，设备保持锁定。请模拟健康检查结果，确认后再释放或隔离。' : '归还后需进行健康复核；借用到期也不会自动释放设备。' }}</p></div><div v-if="selectedLease.status === 'active'" class="action-stack"><button class="button secondary" :disabled="busy" @click="action(() => api.renew(selectedLease.id), '借用已续期 4 小时。', false)"><Icon name="clock" :size="17" />续期 4 小时</button><button class="button primary" :disabled="busy" @click="action(() => api.requestReturn(selectedLease.id), '已提交归还，等待健康复核。', false)">提交归还检查</button></div><div v-else-if="selectedLease.status === 'checking'" class="action-stack"><button class="button danger" :disabled="busy" @click="action(() => api.verifyReturn(selectedLease.id, false), '检查异常，设备已隔离。', false)">模拟检查异常并隔离</button><button class="button primary" :disabled="busy" @click="action(() => api.verifyReturn(selectedLease.id, true), '健康检查通过，设备已释放。', false)">模拟检查通过并释放</button></div></template>
    <template v-else-if="modal.kind === 'maintenance'"><form id="maintenance-form" class="form-stack" @submit.prevent="submitMaintenance"><label>维护环境<select v-model="form.envId" required><option disabled value="">请选择空闲环境</option><option v-for="env in availableEnvs" :key="env.id" :value="env.id">{{ env.name }}</option></select></label><label>维护原因<textarea v-model="form.reason" rows="4" required maxlength="200" placeholder="填写维护原因、计划操作与复核要求" /></label><div class="info-banner"><Icon name="lock" :size="18" /><p>只能对空闲环境开始维护；共享设备的关联环境会同步标为不可借。</p></div></form></template>
    <template v-else-if="modal.kind === 'maintenanceDetail' && selectedMaintenance"><div class="detail-summary"><div><strong class="mono">{{ selectedMaintenance.id }}</strong><p>{{ scopeName(selectedMaintenance) }}</p></div><StatusBadge :status="selectedMaintenance.status" /></div><dl class="detail-grid"><div><dt>处理原因</dt><dd>{{ selectedMaintenance.reason }}</dd></div><div><dt>关联问题</dt><dd class="mono">{{ selectedMaintenance.issue }}</dd></div><div><dt>负责人</dt><dd>{{ selectedMaintenance.user }}</dd></div><div><dt>创建时间</dt><dd>{{ date(selectedMaintenance.date) }}</dd></div></dl><div class="info-banner"><Icon name="shield" :size="18" /><p>以下按钮模拟维护完成后的健康复核，只有通过后环境才恢复可借用。</p></div><div v-if="selectedMaintenance.status !== 'success'" class="action-stack"><button class="button danger" :disabled="busy" @click="action(() => api.verifyMaintenance(selectedMaintenance.id, false), '复核异常，设备保持隔离。')">模拟复核异常</button><button class="button primary" :disabled="busy" @click="action(() => api.verifyMaintenance(selectedMaintenance.id, true), '复核通过，关联环境状态已更新。')">模拟复核通过</button></div></template>
    <template v-else-if="modal.kind === 'run' && selectedRun"><div class="detail-summary"><div><strong class="mono">{{ selectedRun.id }}</strong><p>{{ selectedRun.name }}</p></div><StatusBadge :status="selectedRun.status" /></div><div class="run-progress"><div class="progress-track"><i :style="{ width: selectedRun.progress + '%' }" /></div><strong class="mono">{{ selectedRun.progress }}%</strong></div><dl class="detail-grid"><div><dt>关联环境</dt><dd><button class="inline-link" @click="details(envs.find(e => e.id === selectedRun.envId))">{{ selectedRun.envId }}</button></dd></div><div><dt>版本</dt><dd>{{ selectedRun.version }}</dd></div><div><dt>配置快照</dt><dd class="mono">{{ selectedRun.config }}</dd></div><div><dt>Jenkins 构建标识</dt><dd class="mono">{{ selectedRun.build }} · 示例</dd></div><div><dt>制品引用</dt><dd class="mono break-word">{{ selectedRun.artifact }}</dd></div><div><dt>执行人</dt><dd>{{ selectedRun.user }}</dd></div></dl><h3 class="section-title">执行与证据链</h3><ol class="evidence-list"><li v-for="(label, i) in ['配置及参数快照', '环境与设备独占记录', '构建及制品关联', '测试结果与日志', '清理与释放确认']" :key="label"><span :class="['evidence-step', { done: i < selectedRun.evidence }]"><Icon :name="i < selectedRun.evidence ? 'check' : 'clock'" :size="15" /></span><div><strong>{{ label }}</strong><small>{{ i < selectedRun.evidence ? '已记录 · 演示证据' : '等待当前阶段确认' }}</small></div></li></ol><pre class="log-preview">[mock] run_id={{ selectedRun.id }}
[mock] environment={{ selectedRun.envId }}
[mock] state={{ selectedRun.status }}
[mock] 此处为演示日志，未连接 Jenkins 或真实执行端。</pre><div v-if="['running','reconciling','cleaning'].includes(selectedRun.status)" class="info-banner"><Icon name="info" :size="18" /><p>{{ selectedRun.status === 'reconciling' ? '状态不明，必须对账确认任务终态，再进入清理。' : selectedRun.status === 'cleaning' ? '任务已结束，设备保持锁定，清理通过后才能释放。' : '以下操作模拟执行终态，随后仍需确认设备清理。' }}</p></div><div class="action-stack" v-if="selectedRun.status === 'running'"><button class="button secondary" :disabled="busy" @click="action(() => api.finishRun(selectedRun.id, false), '已记录失败终态，设备待清理。', false)">模拟执行失败</button><button class="button primary" :disabled="busy" @click="action(() => api.finishRun(selectedRun.id, true), '已记录成功终态，设备待清理。', false)">模拟执行成功</button></div><button v-else-if="selectedRun.status === 'reconciling'" class="button primary full-width" :disabled="busy" @click="action(() => api.finishRun(selectedRun.id, true), '已确认任务终态，等待清理。', false)">模拟对账：确认执行已结束</button><div v-else-if="selectedRun.status === 'cleaning'" class="action-stack"><button class="button danger" :disabled="busy" @click="action(() => api.cleanupRun(selectedRun.id, false), '清理异常，设备已隔离。', false)">模拟清理异常</button><button class="button primary" :disabled="busy" @click="action(() => api.cleanupRun(selectedRun.id, true), '清理通过，设备已释放。', false)">确认清理并释放</button></div></template>
    <template v-else-if="modal.kind === 'pipeline' && selectedPipeline"><div class="detail-summary"><div><strong>{{ selectedPipeline.name }}</strong><p class="mono">{{ selectedPipeline.id }} @ {{ selectedPipeline.version }}</p></div><StatusBadge :status="selectedPipeline.status" /></div><pre class="xml-preview">{{ configXml }}</pre><form v-if="selectedPipeline.status === 'published'" id="pipeline-form" class="form-stack" @submit.prevent="startPipeline"><label>选择匹配的空闲环境<select v-model="form.envId" required><option disabled value="">请选择 {{ selectedPipeline.arch }} 环境</option><option v-for="env in pipelineEnvs" :key="env.id" :value="env.id">{{ env.name }} · {{ env.id }}</option></select></label><p v-if="!pipelineEnvs.length" class="subtle-note">暂无匹配架构的可用环境。</p></form><div class="info-banner"><Icon name="info" :size="18" /><p>{{ selectedPipeline.status === 'draft' ? '草稿不能启动任务；当前版本提供只读配置预览。' : '启动仅在本地创建 mock 任务与设备占用，不调用 Jenkins。XML 结构为方案示例。' }}</p></div></template>
    <template v-else-if="modal.kind === 'info'"><div class="table-scroll"><table class="data-table metric-definitions"><thead><tr><th>指标</th><th>定义与范围</th></tr></thead><tbody><tr><td>测试通过率</td><td>所选版本及周期内通过用例执行数 ÷ 有结果的用例执行总数；同一用例多次执行分别计数。</td></tr><tr><td>执行任务</td><td>所选版本及周期内的运行次数；与用例执行数量分开统计。</td></tr><tr><td>环境利用率</td><td>所选版本 7 天固定样本的独占占用设备时长 ÷ 可服务设备时长；不随 3 天选项变化。</td></tr><tr><td>准备耗时 P50</td><td>所选版本 7 天固定样本中，从分配设备到环境就绪的中位耗时。</td></tr><tr><td>证据完整率</td><td>所选版本 7 天固定样本中，同时具备五项证据的运行占比。</td></tr><tr><td>环境资源</td><td>当前本地资源快照，随借用、维护和执行操作变化；不与历史版本统计混用。</td></tr><tr><td>待回归问题</td><td>所选版本的固定问题快照，不随统计周期或本地资源变化。</td></tr><tr><td>版本验证链路</td><td>展示目标系统与关联方式的固定样例，尚未接入 Gerrit、Jenkins、JFrog、RDC。</td></tr></tbody></table></div><div class="info-banner"><Icon name="info" :size="18" /><p>历史窗口固定截至 2026-10-30；mock 操作只改变当前资源快照。未展示部分样本明细，所有数据仅用于前端评审。</p></div></template>
    <template v-else-if="modal.kind === 'settings'"><div class="settings-section"><h3>外观主题</h3><p>主题偏好保存在本浏览器，切换时保留页面与筛选状态。</p><div class="theme-cards"><button v-for="item in themeOptions" :key="item.id" :class="{ active: preference === item.id }" :aria-pressed="preference === item.id" @click="preference = item.id"><Icon :name="item.icon" :size="24" /><strong>{{ item.label }}</strong><span>{{ item.id === 'system' ? '跟随系统外观' : item.id === 'light' ? '清晰明亮的工作空间' : '专注深色数据视图' }}</span></button></div><p class="subtle-note">当前外观：{{ resolved === 'light' ? '浅色' : '深色' }}</p></div><div class="settings-section"><h3>演示数据</h3><p>借用、维护和任务变化保存在本浏览器。可恢复初始样本重新体验；此操作只清除本平台的 mock 数据，属于删除类危险操作。</p><button v-if="!resetConfirm" class="button secondary" :disabled="!isAdmin" :title="isAdmin ? undefined : '需要管理员权限'" @click="resetConfirm = true"><Icon name="refresh" :size="17" />恢复初始演示数据</button><p v-if="!isAdmin" class="subtle-note">普通人员不能重置演示数据，请切换为管理员身份。</p><div v-else-if="resetConfirm" class="action-stack"><button class="button secondary" @click="resetConfirm = false">保留当前数据</button><button class="button danger" :disabled="busy" @click="action(() => api.reset(), '已恢复初始演示数据。')">确认重置演示数据</button></div></div></template>
    <template v-else-if="modal.kind === 'notifications'"><div class="notice-banner"><Icon name="bell" :size="22" /><div><strong>工作空间待办</strong><p>以下事项来自当前 mock 资源快照。</p></div></div><button class="attention-item" @click="navigate('leases')"><Icon name="lock" /><span>我的借用与待健康复核</span><Icon name="chevron" :size="17" /></button><button class="attention-item" @click="navigate('runs')"><Icon name="clipboard" /><span>{{ snapshot.runs.filter(r => ['cleaning','reconciling'].includes(r.status)).length }} 个任务需要清理或对账</span><Icon name="chevron" :size="17" /></button><button class="attention-item" @click="navigate('maintenance')"><Icon name="tool" /><span>{{ snapshot.maintenance.filter(m => m.status !== 'success').length }} 条维护 / 隔离记录待处理</span><Icon name="chevron" :size="17" /></button></template>
    <template v-else-if="modal.kind === 'issue' && selectedIssue"><div class="detail-summary"><div><strong class="mono">{{ selectedIssue.id }}</strong><p>{{ selectedIssue.title }}</p></div><StatusBadge :status="selectedIssue.status" /></div><dl class="detail-grid"><div><dt>负责人</dt><dd>{{ selectedIssue.owner }}</dd></div><div><dt>优先级</dt><dd>{{ selectedIssue.severity }}</dd></div><div><dt>所属模块</dt><dd>{{ selectedIssue.module }}</dd></div><div><dt>关联版本</dt><dd>OS 24.03 RC2</dd></div></dl><div class="info-banner"><Icon name="link" :size="18" /><p>此处演示问题的追溯信息；RDC 尚未接入，当前不执行真实问题流转。</p></div></template>
    <template v-else-if="modal.kind === 'identity'"><div class="notice-banner"><Icon name="user" :size="22" /><div><strong>演示身份切换</strong><p>切换后以所选身份执行操作，用于验证权限拦截；只影响本浏览器的演示数据。</p></div></div><div class="identity-list"><button v-for="user in snapshot.users.filter(u => u.status === 'active')" :key="user.id" :class="['identity-item', { current: user.id === currentUser?.id }]" @click="user.id === currentUser?.id || switchUser(user)"><span class="avatar">{{ user.name.slice(0, 1) }}</span><div><strong>{{ user.name }}</strong><small>{{ user.title }}</small></div><span class="badge" :class="user.role === 'admin' ? 'tone-amber' : 'tone-blue'">{{ roleLabel(user) }}</span><Icon v-if="user.id === currentUser?.id" name="check" :size="17" /></button></div><p class="page-footnote">{{ isAdmin ? '停用人员不出现在此列表；如需调整角色或停用状态，前往人员管理页。' : '停用人员不出现在此列表。' }}</p></template>
    <template v-else-if="modal.kind === 'userAdd'"><form id="user-add-form" class="form-stack" @submit.prevent="submitUserAdd"><label>人员姓名<input v-model="userForm.name" required maxlength="20" :disabled="busy" placeholder="例如：赵倩" /></label><label>初始角色<select v-model="userForm.role" :disabled="busy"><option value="member">普通人员（除删除类危险操作外均可执行）</option><option value="admin">管理员（全部权限）</option></select></label><div class="info-banner"><Icon name="shield" :size="18" /><p>权限模型从简：管理员拥有全部权限；普通人员仅删除类危险操作（移出资源池、重置演示数据）与人员管理修改受限。</p></div></form></template>
    <template #footer><span class="subtle-note"><Icon v-if="busy" name="refresh" :size="14" :class="{ spinning: busy }" />{{ busy ? '正在处理…' : '仅修改本地演示数据' }}</span><div class="modal-footer-actions"><button class="button secondary" :disabled="busy" @click="closeModal">{{ ['borrow','maintenance','environmentEdit','deviceEdit','archiveResource'].includes(modal.kind) ? '取消' : '关闭' }}</button><template v-if="modal.kind === 'environment' && selectedEnv"><button class="button secondary" @click="editResource('environment', selectedEnv)">编辑环境</button><button class="button danger" :disabled="!isAdmin" :title="isAdmin ? undefined : '需要管理员权限'" @click="confirmArchive('environment', selectedEnv)">移出资源池</button><button v-if="selectedEnv.status === 'available'" class="button primary" @click="borrow(selectedEnv)">申请借用</button><button v-else-if="selectedEnv.run" class="button primary" @click="runDetails(selectedEnv.run)">查看执行</button><button v-else-if="selectedEnv.lease" class="button primary" @click="leaseDetails(selectedEnv.lease)">管理借用</button></template><button v-if="modal.kind === 'borrow'" class="button primary" type="submit" form="borrow-form" :disabled="busy || borrowSelectionInvalid || !form.purpose?.trim()">确认借用 {{ form.envIds.length || '' }}{{ form.envIds.length ? ' 个环境' : '' }}</button><template v-if="modal.kind === 'borrowResult' && borrowResult"><button v-if="borrowResult.outcomes.some(item => item.status === 'skipped')" class="button secondary" @click="retrySkipped">调整未借到的环境</button><button v-if="borrowResult.lease" class="button primary" @click="leaseDetails(borrowResult.lease)">管理成功借用</button></template><button v-if="['environmentEdit','deviceEdit'].includes(modal.kind)" class="button primary" type="submit" form="resource-form" :disabled="busy || resourceEditingLocked">保存资源</button><button v-if="modal.kind === 'archiveResource'" class="button danger" :disabled="busy" @click="archiveResource">确认移出</button><button v-if="modal.kind === 'maintenance'" class="button primary" type="submit" form="maintenance-form" :disabled="busy || !form.envId || !form.reason?.trim()">开始维护</button><button v-if="modal.kind === 'pipeline' && selectedPipeline.status === 'published'" class="button primary" type="submit" form="pipeline-form" :disabled="busy || !form.envId">启动演示任务</button><button v-if="modal.kind === 'userAdd'" class="button primary" type="submit" form="user-add-form" :disabled="busy || !userForm.name?.trim()">确认新增</button></div></template>
  </ModalShell>
</template>
