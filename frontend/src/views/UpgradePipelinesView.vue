<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import Icon from '../components/Icon.vue'
import MetricCard from '../components/MetricCard.vue'
import StatusBadge from '../components/StatusBadge.vue'
import ModalShell from '../components/ModalShell.vue'
import { getUpgradeStore, UPGRADE_STAGES } from '../mock/upgrade.js'

// 升级流水线（规划页 · Mock 实现）：配置与运行数据全部来自浏览器演示 store（argus.upgrade.v1）。
// 环境列表来自当前资源快照，仅用于演示选择；演示运行不占用真实设备、不提交 Jenkins。
const props = defineProps({ envs: Array, currentUser: Object, canAdmin: Boolean })
const store = getUpgradeStore()
const snapshot = ref(null), loading = ref(true), loadError = ref(''), busy = ref(false), modalError = ref(''), storeWarning = ref(false)
const query = ref(''), state = ref('all'), arch = ref('all')
const modal = ref(null), form = ref(blankForm()), startForm = ref({ pipelineId: '', envId: '' })

function blankForm() { return { id: '', name: '', description: '', arch: 'x86_64', baseVersion: 'OS 24.03', targetVersion: '', packageRef: '', packageSha256: '' } }
const actor = computed(() => ({ name: props.currentUser?.name || '演示用户', role: props.currentUser?.role || 'member' }))
const pipelines = computed(() => snapshot.value?.pipelines || [])
const runs = computed(() => snapshot.value?.runs || [])
const filtered = computed(() => pipelines.value.filter(pipeline =>
  `${pipeline.id} ${pipeline.name} ${pipeline.description} ${pipeline.baseVersion} ${pipeline.targetVersion}`.toLowerCase().includes(query.value.trim().toLowerCase())
  && (state.value === 'all' || state.value === pipeline.status)
  && (arch.value === 'all' || arch.value === pipeline.arch)))
const recentRuns = computed(() => runs.value.filter(run =>
  `${run.id} ${run.pipelineName} ${run.envName}`.toLowerCase().includes(query.value.trim().toLowerCase())
  && (arch.value === 'all' || arch.value === run.arch)).slice(0, 8))
const counts = computed(() => ({
  configs: pipelines.value.length,
  published: pipelines.value.filter(p => p.status === 'published').length,
  running: runs.value.filter(r => r.status === 'running').length,
  success: runs.value.filter(r => r.status === 'success').length,
}))
const idleEnvs = pipeline => (props.envs || []).filter(env => env.status === 'available' && env.arch === pipeline.arch)
const startEnvs = computed(() => idleEnvs(pipelines.value.find(p => p.id === startForm.value.pipelineId)))
const selectedRun = computed(() => runs.value.find(run => run.id === modal.value?.id))
const modalTitle = computed(() => ({ edit: form.value?.id ? '编辑升级配置（草稿）' : '新建升级配置', start: '启动升级演示', run: '升级运行详情', config: '升级配置详情', publish: '发布升级配置' })[modal.value?.kind] || '')
const stageTone = { pending: 'muted', running: 'blue', success: 'green', failed: 'red' }
const stageLabel = { pending: '未开始', running: '执行中', success: '成功', failed: '失败' }

async function reload() { const result = await store.load(); snapshot.value = result.snapshot; storeWarning.value = result.persistenceWarning }
async function load() {
  loading.value = true; loadError.value = ''
  try { await reload() } catch (error) { loadError.value = error.message || '演示数据加载失败。' } finally { loading.value = false }
}
let timer
onMounted(async () => { await load(); timer = setInterval(async () => {
  const running = runs.value.filter(run => run.status === 'running')
  if (!running.length) return
  for (const run of running) { try { await store.tickUpgradeRun(run.id) } catch { /* 已被手动推进或结束 */ } }
  try { await reload() } catch { /* 下次再试 */ }
}, 1500) })
onBeforeUnmount(() => clearInterval(timer))

function openEdit(pipeline = null) { form.value = pipeline ? { ...pipeline } : blankForm(); open('edit') }
function openStart(pipeline) { startForm.value = { pipelineId: pipeline?.id || pipelines.value.find(p => p.status === 'published')?.id || '', envId: '' }; open('start') }
function open(kind, id = null) { modalError.value = ''; modal.value = { kind, id } }
function close() { if (!busy.value) { modal.value = null; modalError.value = '' } }
async function run(action, message) {
  if (busy.value) return
  busy.value = true; modalError.value = ''
  try { await action(); await reload(); if (message) modal.value = null; modalError.value = ''; return true }
  catch (error) { modalError.value = error.message || '操作未完成，请重试。'; return false }
  finally { busy.value = false }
}
const saveDraft = () => run(async () => { await store.saveUpgradePipeline({ ...form.value, actor: actor.value }) }, true)
const publish = () => run(async () => { await store.publishUpgradePipeline(modal.value.id, actor.value) }, true)
async function copyPipeline(pipeline) {
  if (await run(() => store.copyUpgradePipeline(pipeline.id, actor.value))) notifyLocal('已复制为独立草稿。')
}
const startRun = () => run(async () => {
  const pipeline = pipelines.value.find(p => p.id === startForm.value.pipelineId)
  const env = (props.envs || []).find(item => item.id === startForm.value.envId)
  await store.startUpgradeRun({ pipelineId: startForm.value.pipelineId, envId: startForm.value.envId, envName: env?.name, envArch: env?.arch, actor: actor.value })
}, true)
const advance = () => run(async () => { await store.tickUpgradeRun(modal.value.id) }, false)
const markFailed = () => run(async () => { await store.failUpgradeRun(modal.value.id, { reason: '界面手动标记失败：模拟升级中断' }) }, true)
let toastTimer, toast = ref(null)
function notifyLocal(message) { toast.value = message; clearTimeout(toastTimer); toastTimer = setTimeout(() => { toast.value = null }, 3800) }
</script>
<template>
  <div class="page-heading"><div><div class="eyebrow">UPGRADE PIPELINES</div><h1>升级流水线<span class="demo-tag">演示数据 · Mock 实现</span></h1><p>基线安装 → 前置检查 → 包分发 → 执行升级 → 版本校验 → 升级后冒烟；验证升级包从基线版本到目标版本的完整链路。</p></div><div class="heading-actions"><button class="button primary" @click="openEdit()"><Icon name="plus" :size="17" />新建升级配置</button></div></div>
  <div class="metrics-grid environment-metrics">
    <MetricCard label="升级配置" :value="counts.configs" detail="草稿与不可变发布版本" icon="flow" />
    <MetricCard label="已发布" :value="counts.published" detail="发布后可启动升级演示" icon="check" tone="green" />
    <MetricCard label="运行中升级" :value="counts.running" detail="逐阶段推进 · 本地模拟" icon="refresh" tone="amber" />
    <MetricCard label="累计升级成功" :value="counts.success" detail="含固定历史样本" icon="download" tone="teal" />
  </div>
  <div v-if="loading" class="loading-state" role="status"><span class="spinner" /><p>正在加载升级演示数据…</p></div>
  <div v-else-if="loadError" class="empty-state panel" role="alert"><Icon name="alert" :size="34" /><p>{{ loadError }}</p><button class="button secondary small" @click="load">重新加载</button></div>
  <template v-else>
    <section class="panel workspace-panel" aria-label="升级配置列表">
      <div class="workspace-toolbar"><label class="search-field"><Icon name="search" :size="18" /><input v-model="query" aria-label="搜索升级配置" placeholder="搜索名称、版本或说明" /><button v-if="query" class="icon-button" aria-label="清空搜索" @click="query = ''"><Icon name="close" :size="14" /></button></label><label class="select-label">状态<select v-model="state" aria-label="筛选配置状态"><option value="all">全部状态</option><option value="published">已发布</option><option value="draft">草稿</option></select></label><label class="select-label">架构<select v-model="arch" aria-label="筛选架构"><option value="all">全部架构</option><option>ARM64</option><option>x86_64</option></select></label><span class="subtle-note toolbar-end">{{ filtered.length }} 个配置 · {{ recentRuns.length }} 条演示运行</span></div>
      <div v-if="filtered.length" class="pipeline-list"><article v-for="pipeline in filtered" :key="pipeline.id" class="pipeline-config"><div class="resource-icon"><Icon name="download" :size="26" /></div><div><h3>{{ pipeline.name }}<span class="version-tag mono">{{ pipeline.arch }}</span></h3><p>{{ pipeline.description || '未填写描述' }}</p><div class="metadata"><span class="mono">{{ pipeline.id }}</span><span class="version-path"><span class="mono">{{ pipeline.baseVersion }}</span><Icon name="arrow" :size="14" /><span class="mono">{{ pipeline.targetVersion }}</span></span><span>· 更新于 {{ pipeline.updatedAt }}</span></div><div class="metadata"><span class="badge" :class="idleEnvs(pipeline).length ? 'tone-blue' : 'tone-muted'">{{ idleEnvs(pipeline).length ? `匹配 ${idleEnvs(pipeline).length} 个空闲环境` : '暂无匹配空闲环境' }}</span><span class="mono subtle-note">{{ pipeline.packageRef }}</span></div></div><StatusBadge :status="pipeline.status" /><div class="table-actions"><button v-if="pipeline.status === 'draft'" class="button secondary small" @click="openEdit(pipeline)">编辑</button><button v-if="pipeline.status === 'draft' && canAdmin" class="button primary small" @click="open('publish', pipeline.id)"><Icon name="check" :size="14" />发布</button><button class="button secondary small" @click="copyPipeline(pipeline)">复制</button><button class="button secondary small" @click="open('config', pipeline.id)">查看配置</button><button class="button primary small" :disabled="pipeline.status !== 'published'" :title="pipeline.status === 'published' ? undefined : '发布后才能启动升级'" @click="openStart(pipeline)">启动升级</button></div></article></div>
      <div v-else class="empty-state"><Icon name="search" :size="36" /><h3>没有匹配的升级配置</h3><button class="button secondary small" @click="query = ''; state = 'all'; arch = 'all'">清空筛选</button></div>
    </section>
    <section class="panel workspace-panel" aria-label="最近升级运行">
      <div class="panel-heading quality-heading"><h2>最近升级运行</h2><span class="subtle-note">阶段自动推进 · 仅本地演示</span></div>
      <div class="table-scroll"><table class="data-table"><thead><tr><th>运行 / 配置</th><th>目标环境</th><th>版本路径</th><th>当前阶段</th><th>状态</th><th>开始时间</th><th>操作</th></tr></thead><tbody><tr v-for="run in recentRuns" :key="run.id"><td><button class="inline-link mono" @click="open('run', run.id)">{{ run.id }}</button><small>{{ run.pipelineName }}</small></td><td>{{ run.envName }}<small class="mono">{{ run.envId }}</small></td><td><span class="version-path"><span class="mono">{{ run.baseVersion }}</span><Icon name="arrow" :size="13" /><span class="mono">{{ run.versionAfter || run.targetVersion }}</span></span></td><td>{{ run.stages.find(stage => ['running', 'failed'].includes(stage.status))?.name || (run.status === 'success' ? '全部完成' : '—') }}</td><td><StatusBadge :status="run.status" :label="run.status === 'success' ? '升级成功' : undefined" /></td><td class="mono">{{ run.started }}</td><td><button class="text-button" @click="open('run', run.id)">运行详情<Icon name="chevron" :size="13" /></button></td></tr></tbody></table><div v-if="!recentRuns.length" class="empty-state"><Icon name="download" :size="32" /><p>暂无升级运行记录</p></div></div>
    </section>
    <div class="page-footnote"><Icon name="info" :size="15" /><span>升级流水线为规划页的 Mock 实现：运行由浏览器逐阶段模拟，不占用真实设备、不提交 Jenkins、不影响主资源池状态；环境列表仅用于演示选择。升级包必须提供 SHA-256 摘要用于完整性校验。</span></div>
  </template>

  <ModalShell v-if="modal" :title="modalTitle" subtitle="升级流水线演示数据 · Mock 实现" wide @close="close">
    <p v-if="modalError" class="form-error" role="alert"><Icon name="alert" :size="17" />{{ modalError }}</p>
    <template v-if="modal.kind === 'edit'">
      <form id="upgrade-form" class="form-stack" @submit.prevent="saveDraft">
        <label>配置名称<input v-model="form.name" required maxlength="60" :disabled="busy" placeholder="例如：OS 24.03 → 24.04 升级验证" /></label>
        <label>描述<textarea v-model="form.description" rows="2" maxlength="200" :disabled="busy" placeholder="说明升级场景与验证范围" /></label>
        <label>处理器架构<select v-model="form.arch" :disabled="busy"><option>ARM64</option><option>x86_64</option></select></label>
        <div class="form-row2">
          <label>基线版本<input v-model="form.baseVersion" required maxlength="40" :disabled="busy" /></label>
          <label>目标版本<input v-model="form.targetVersion" required maxlength="40" :disabled="busy" placeholder="例如：OS 24.04" /></label>
        </div>
        <label>升级包引用<input v-model="form.packageRef" required maxlength="200" :disabled="busy" placeholder="jfrog://argus/os-upgrade/…" class="mono" /></label>
        <label>升级包 SHA-256 摘要<input v-model="form.packageSha256" required maxlength="64" :disabled="busy" placeholder="64 位十六进制" class="mono" /><span class="field-hint">发布前必须提供包摘要，运行时按快照校验完整性。</span></label>
        <div class="info-banner"><Icon name="flow" :size="18" /><p>执行阶段固定为：{{ UPGRADE_STAGES.map(stage => stage.name).join(' → ') }}。</p></div>
      </form>
    </template>
    <template v-else-if="modal.kind === 'start'">
      <form id="upgrade-start-form" class="form-stack" @submit.prevent="startRun">
        <label>升级配置<select v-model="startForm.pipelineId" :disabled="busy" required><option disabled value="">请选择已发布配置</option><option v-for="pipeline in pipelines.filter(p => p.status === 'published')" :key="pipeline.id" :value="pipeline.id">{{ pipeline.name }} · {{ pipeline.baseVersion }} → {{ pipeline.targetVersion }}</option></select></label>
        <label>目标环境（{{ pipelines.find(p => p.id === startForm.pipelineId)?.arch }} · 空闲）<select v-model="startForm.envId" :disabled="busy" required><option disabled value="">请选择匹配架构的空闲环境</option><option v-for="env in startEnvs" :key="env.id" :value="env.id">{{ env.name }} · {{ env.id }}</option></select></label>
        <p v-if="startForm.pipelineId && !startEnvs.length" class="subtle-note">暂无匹配架构的空闲环境，可先在环境资源页释放或新增环境。</p>
        <div class="info-banner"><Icon name="info" :size="18" /><p>演示运行会按六个阶段自动推进（约十几秒），结束后记录升级后版本；不会改动真实设备与主资源池状态。</p></div>
      </form>
    </template>
    <template v-else-if="modal.kind === 'run' && selectedRun">
      <div class="detail-summary"><div><strong class="mono">{{ selectedRun.id }}</strong><p>{{ selectedRun.pipelineName }} · {{ selectedRun.envName }}</p></div><StatusBadge :status="selectedRun.status" :label="selectedRun.status === 'success' ? '升级成功' : undefined" /></div>
      <dl class="detail-grid"><div><dt>版本路径</dt><dd class="mono">{{ selectedRun.baseVersion }} → {{ selectedRun.versionAfter || selectedRun.targetVersion }}</dd></div><div><dt>升级包</dt><dd class="mono break-word">{{ selectedRun.packageRef }}</dd></div><div><dt>包摘要</dt><dd class="mono break-word">{{ selectedRun.packageSha256.slice(0, 16) }}…</dd></div><div><dt>执行人</dt><dd>{{ selectedRun.user }}</dd></div></dl>
      <h3 class="section-title">执行阶段</h3>
      <ol class="stage-list"><li v-for="stage in selectedRun.stages" :key="stage.key"><span :class="['stage-step', stage.status]"><Icon :name="stage.status === 'success' ? 'check' : stage.status === 'failed' ? 'alert' : stage.status === 'running' ? 'refresh' : 'clock'" :size="15" /></span><div><strong>{{ stage.name }}</strong><small>{{ UPGRADE_STAGES.find(s => s.key === stage.key)?.detail }}</small><small v-if="stage.error" class="color-red" role="status">{{ stage.error }}</small></div><StatusBadge :status="stage.status" :label="stageLabel[stage.status]" /><span v-if="stage.duration" class="stage-duration mono">{{ stage.duration }}s</span></li></ol>
      <pre v-if="selectedRun.error" class="log-preview">[mock] run_id={{ selectedRun.id }}
[mock] stage_error={{ selectedRun.error }}
[mock] 演示日志：未连接真实升级通道。</pre>
      <div v-if="selectedRun.status === 'running'" class="info-banner"><Icon name="refresh" :size="18" /><p>阶段按演示节奏自动推进，也可以手动推进或标记失败；运行数据仅保存在本浏览器。</p></div>
      <div v-else class="info-banner"><Icon name="info" :size="18" /><p>{{ selectedRun.status === 'success' ? '演示升级完成；真实链路中还需人工确认升级后健康复核。' : '演示升级失败并保留失败阶段；真实链路会自动回退基线版本并建立维护记录。' }}</p></div>
      <div v-if="selectedRun.status === 'running'" class="action-stack"><button class="button danger" :disabled="busy" @click="markFailed">模拟升级失败</button><button class="button secondary" :disabled="busy" @click="advance"><Icon name="refresh" :size="16" />推进下一阶段</button></div>
    </template>
    <template v-else-if="modal.kind === 'config' && pipelines.find(p => p.id === modal.id)">
      <div class="detail-summary"><div><strong>{{ pipelines.find(p => p.id === modal.id).name }}</strong><p class="mono">{{ pipelines.find(p => p.id === modal.id).id }} · {{ pipelines.find(p => p.id === modal.id).arch }}</p></div><StatusBadge :status="pipelines.find(p => p.id === modal.id).status" /></div>
      <dl class="detail-grid"><div><dt>版本路径</dt><dd class="mono">{{ pipelines.find(p => p.id === modal.id).baseVersion }} → {{ pipelines.find(p => p.id === modal.id).targetVersion }}</dd></div><div><dt>升级包引用</dt><dd class="mono break-word">{{ pipelines.find(p => p.id === modal.id).packageRef }}</dd></div><div><dt>包 SHA-256</dt><dd class="mono break-word">{{ pipelines.find(p => p.id === modal.id).packageSha256 }}</dd></div><div><dt>创建人</dt><dd>{{ pipelines.find(p => p.id === modal.id).createdBy }} · {{ pipelines.find(p => p.id === modal.id).createdAt }}</dd></div></dl>
      <h3 class="section-title">执行阶段（固定顺序）</h3>
      <ol class="stage-list"><li v-for="stage in UPGRADE_STAGES" :key="stage.key"><span class="stage-step"><Icon name="flow" :size="14" /></span><div><strong>{{ stage.name }}</strong><small>{{ stage.detail }}</small></div><span class="stage-duration mono">约 {{ stage.duration }}s</span></li></ol>
    </template>
    <template v-else-if="modal.kind === 'publish' && pipelines.find(p => p.id === modal.id)">
      <div class="notice-banner"><Icon name="check" :size="24" /><div><strong>{{ pipelines.find(p => p.id === modal.id).name }}</strong><p>发布后配置不可再修改；运行时按当前包引用与摘要快照执行校验。</p></div></div>
      <p class="subtle-note">发布属于管理员操作；草稿可随时继续编辑。</p>
    </template>
    <template #footer><span class="subtle-note"><Icon v-if="busy" name="refresh" :size="14" :class="{ spinning: busy }" />{{ busy ? '正在处理…' : '浏览器演示数据' }}</span><div class="modal-footer-actions">
      <button class="button secondary" :disabled="busy" @click="close">取消</button>
      <button v-if="modal.kind === 'edit'" class="button primary" type="submit" form="upgrade-form" :disabled="busy">保存草稿</button>
      <button v-if="modal.kind === 'start'" class="button primary" type="submit" form="upgrade-start-form" :disabled="busy || !startForm.pipelineId || !startForm.envId">启动升级演示</button>
      <button v-if="modal.kind === 'publish'" class="button primary" :disabled="busy" @click="publish">确认发布</button>
    </div></template>
  </ModalShell>
  <Transition name="toast"><div v-if="toast" class="toast-message" role="status"><Icon name="check" :size="18" /><span>{{ toast }}</span></div></Transition>
</template>
