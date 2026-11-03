<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import Icon from '../components/Icon.vue'
import MetricCard from '../components/MetricCard.vue'
import StatusBadge from '../components/StatusBadge.vue'
import ModalShell from '../components/ModalShell.vue'
import { getPerfStore, PERF_DIRECTIONS } from '../mock/perf.js'

// 性能流水线（规划页 · Mock 实现）：指标配置与采集结果保存在浏览器演示 store（argus.perf.v1），
// 与「性能数据看板」共用同一份数据；不占用真实设备、不连接采集代理。
const props = defineProps({ envs: Array, currentUser: Object, canAdmin: Boolean })
const store = getPerfStore()
const snapshot = ref(null), loading = ref(true), loadError = ref(''), busy = ref(false), modalError = ref('')
const query = ref(''), state = ref('all'), arch = ref('all')
const modal = ref(null), form = ref(blankForm()), startForm = ref({ pipelineId: '', envId: '', version: 'OS 24.03 RC2' })

function blankMetrics() { return [{ key: '', name: '', unit: 'ms', direction: 'lower', baseline: '', threshold: 5 }] }
function blankForm() { return { id: '', name: '', description: '', arch: 'x86_64', metrics: blankMetrics() } }
const actor = computed(() => ({ name: props.currentUser?.name || '演示用户', role: props.currentUser?.role || 'member' }))
const pipelines = computed(() => snapshot.value?.pipelines || [])
const runs = computed(() => snapshot.value?.runs || [])
const filtered = computed(() => pipelines.value.filter(pipeline =>
  `${pipeline.id} ${pipeline.name} ${pipeline.description}`.toLowerCase().includes(query.value.trim().toLowerCase())
  && (state.value === 'all' || state.value === pipeline.status)
  && (arch.value === 'all' || arch.value === pipeline.arch)))
const recentRuns = computed(() => runs.value.filter(run =>
  `${run.id} ${run.pipelineName} ${run.envName} ${run.version}`.toLowerCase().includes(query.value.trim().toLowerCase())
  && (arch.value === 'all' || arch.value === run.arch)).slice(0, 8))
const counts = computed(() => ({
  configs: pipelines.value.length,
  published: pipelines.value.filter(p => p.status === 'published').length,
  running: runs.value.filter(r => r.status === 'running').length,
  regress: runs.value.reduce((sum, run) => sum + (run.results || []).filter(item => item.verdict === 'regress').length, 0),
}))
const idleEnvs = pipeline => (props.envs || []).filter(env => env.status === 'available' && env.arch === pipeline.arch)
const startEnvs = computed(() => idleEnvs(pipelines.value.find(p => p.id === startForm.value.pipelineId)))
const selectedRun = computed(() => runs.value.find(run => run.id === modal.value?.id))
const modalTitle = computed(() => ({ edit: form.value?.id ? '编辑性能配置（草稿）' : '新建性能配置', start: '启动性能采集', run: '采集运行详情', config: '性能配置详情', publish: '发布性能配置' })[modal.value?.kind] || '')
const verdictMeta = { pass: { label: '达标', tone: 'green' }, improve: { label: '优化', tone: 'teal' }, regress: { label: '回归', tone: 'red' } }
const fmtDelta = value => `${value > 0 ? '+' : ''}${value}%`

async function reload() { const result = await store.load(); snapshot.value = result.snapshot }
async function load() {
  loading.value = true; loadError.value = ''
  try { await reload() } catch (error) { loadError.value = error.message || '演示数据加载失败。' } finally { loading.value = false }
}
let timer
onMounted(async () => { await load(); timer = setInterval(async () => {
  const running = runs.value.filter(run => run.status === 'running')
  if (!running.length) return
  for (const run of running) { try { await store.tickPerfRun(run.id) } catch { /* 已结束 */ } }
  try { await reload() } catch { /* 下次再试 */ }
}, 1500) })
onBeforeUnmount(() => clearInterval(timer))

function openEdit(pipeline = null) {
  form.value = pipeline ? { ...pipeline, metrics: pipeline.metrics.map(m => ({ ...m, baseline: String(m.baseline), threshold: String(m.threshold) })) } : blankForm()
  open('edit')
}
function openStart(pipeline) { startForm.value = { pipelineId: pipeline?.id || pipelines.value.find(p => p.status === 'published')?.id || '', envId: '', version: 'OS 24.03 RC2' }; open('start') }
function open(kind, id = null) { modalError.value = ''; modal.value = { kind, id } }
function close() { if (!busy.value) { modal.value = null; modalError.value = '' } }
async function run(action, closeAfter = true) {
  if (busy.value) return false
  busy.value = true; modalError.value = ''
  try { await action(); await reload(); if (closeAfter) { modal.value = null; modalError.value = '' } return true }
  catch (error) { modalError.value = error.message || '操作未完成，请重试。'; return false }
  finally { busy.value = false }
}
const saveDraft = () => run(async () => {
  await store.savePerfPipeline({
    ...form.value,
    metrics: form.value.metrics.map(item => ({ ...item, baseline: Number(item.baseline), threshold: Number(item.threshold) })),
    actor: actor.value,
  })
})
const publish = () => run(async () => { await store.publishPerfPipeline(modal.value.id, actor.value) })
async function copyPipeline(pipeline) { if (await run(() => store.copyPerfPipeline(pipeline.id, actor.value), false)) notifyLocal('已复制为独立草稿。') }
const startRun = () => run(async () => {
  const pipeline = pipelines.value.find(p => p.id === startForm.value.pipelineId)
  const env = (props.envs || []).find(item => item.id === startForm.value.envId)
  await store.startPerfRun({ pipelineId: startForm.value.pipelineId, envId: startForm.value.envId, envName: env?.name, envArch: env?.arch, version: startForm.value.version, actor: actor.value })
})
const advance = () => run(async () => { await store.tickPerfRun(modal.value.id) }, false)
const markFailed = () => run(async () => { await store.failPerfRun(modal.value.id, { reason: '界面手动标记失败：模拟采集异常' }) })
function addMetric() { if (form.value.metrics.length < 8) form.value.metrics.push(blankMetrics()[0]) }
function removeMetric(index) { if (form.value.metrics.length > 1) form.value.metrics.splice(index, 1) }
let toastTimer, toast = ref(null)
function notifyLocal(message) { toast.value = message; clearTimeout(toastTimer); toastTimer = setTimeout(() => { toast.value = null }, 3800) }
</script>
<template>
  <div class="page-heading"><div><div class="eyebrow">PERFORMANCE PIPELINES</div><h1>性能流水线<span class="demo-tag">演示数据 · Mock 实现</span></h1><p>定义采集指标、基线与允许波动，发布后在空闲环境启动演示采集；结果进入性能数据看板做版本对比。</p></div><div class="heading-actions"><button class="button primary" @click="openEdit()"><Icon name="plus" :size="17" />新建性能配置</button></div></div>
  <div class="metrics-grid environment-metrics">
    <MetricCard label="性能配置" :value="counts.configs" detail="指标 · 基线 · 允许波动" icon="flow" />
    <MetricCard label="已发布" :value="counts.published" detail="发布后可启动采集" icon="check" tone="green" />
    <MetricCard label="运行中采集" :value="counts.running" detail="本地模拟推进" icon="pulse" tone="amber" />
    <MetricCard label="累计回归项" :value="counts.regress" detail="超出基线允许波动的指标" icon="alert" tone="red" />
  </div>
  <div v-if="loading" class="loading-state" role="status"><span class="spinner" /><p>正在加载性能演示数据…</p></div>
  <div v-else-if="loadError" class="empty-state panel" role="alert"><Icon name="alert" :size="34" /><p>{{ loadError }}</p><button class="button secondary small" @click="load">重新加载</button></div>
  <template v-else>
    <section class="panel workspace-panel" aria-label="性能配置列表">
      <div class="workspace-toolbar"><label class="search-field"><Icon name="search" :size="18" /><input v-model="query" aria-label="搜索性能配置" placeholder="搜索名称或说明" /><button v-if="query" class="icon-button" aria-label="清空搜索" @click="query = ''"><Icon name="close" :size="14" /></button></label><label class="select-label">状态<select v-model="state" aria-label="筛选配置状态"><option value="all">全部状态</option><option value="published">已发布</option><option value="draft">草稿</option></select></label><label class="select-label">架构<select v-model="arch" aria-label="筛选架构"><option value="all">全部架构</option><option>ARM64</option><option>x86_64</option></select></label><span class="subtle-note toolbar-end">{{ filtered.length }} 个配置 · {{ recentRuns.length }} 条演示采集</span></div>
      <div v-if="filtered.length" class="pipeline-list"><article v-for="pipeline in filtered" :key="pipeline.id" class="pipeline-config"><div class="resource-icon"><Icon name="pulse" :size="26" /></div><div><h3>{{ pipeline.name }}<span class="version-tag mono">{{ pipeline.arch }}</span></h3><p>{{ pipeline.description || '未填写描述' }}</p><div class="metadata"><span class="mono">{{ pipeline.id }}</span><span>· {{ pipeline.metrics.length }} 项指标</span><span>· {{ pipeline.metrics.map(m => m.name).join(' / ') }}</span><span>· 更新于 {{ pipeline.updatedAt }}</span></div></div><StatusBadge :status="pipeline.status" /><div class="table-actions"><button v-if="pipeline.status === 'draft'" class="button secondary small" @click="openEdit(pipeline)">编辑</button><button v-if="pipeline.status === 'draft' && canAdmin" class="button primary small" @click="open('publish', pipeline.id)"><Icon name="check" :size="14" />发布</button><button class="button secondary small" @click="copyPipeline(pipeline)">复制</button><button class="button secondary small" @click="open('config', pipeline.id)">查看配置</button><button class="button primary small" :disabled="pipeline.status !== 'published'" :title="pipeline.status === 'published' ? undefined : '发布后才能启动采集'" @click="openStart(pipeline)">启动采集</button></div></article></div>
      <div v-else class="empty-state"><Icon name="search" :size="36" /><h3>没有匹配的性能配置</h3><button class="button secondary small" @click="query = ''; state = 'all'; arch = 'all'">清空筛选</button></div>
    </section>
    <section class="panel workspace-panel" aria-label="最近性能采集">
      <div class="panel-heading quality-heading"><h2>最近性能采集</h2><span class="subtle-note">进度本地模拟 · 结果进入性能数据看板</span></div>
      <div class="table-scroll"><table class="data-table"><thead><tr><th>运行 / 配置</th><th>环境</th><th>被测版本</th><th>状态</th><th>回归项</th><th>开始时间</th><th>操作</th></tr></thead><tbody><tr v-for="run in recentRuns" :key="run.id"><td><button class="inline-link mono" @click="open('run', run.id)">{{ run.id }}</button><small>{{ run.pipelineName }}</small></td><td>{{ run.envName }}<small class="mono">{{ run.envId }}</small></td><td class="mono">{{ run.version }}</td><td><StatusBadge :status="run.status" :label="run.status === 'success' ? '采集完成' : undefined" /></td><td><span class="badge" :class="(run.results || []).some(r => r.verdict === 'regress') ? 'tone-red' : 'tone-muted'">{{ (run.results || []).filter(r => r.verdict === 'regress').length }} 项</span></td><td class="mono">{{ run.started }}</td><td><button class="text-button" @click="open('run', run.id)">运行详情<Icon name="chevron" :size="13" /></button></td></tr></tbody></table><div v-if="!recentRuns.length" class="empty-state"><Icon name="pulse" :size="32" /><p>暂无性能采集记录</p></div></div>
    </section>
    <div class="page-footnote"><Icon name="info" :size="15" /><span>性能流水线为规划页的 Mock 实现：采集进度与指标值由浏览器确定性模拟（同一运行结果可复现），不占用真实设备、不连接采集代理；聚合分析见「性能数据看板」。</span></div>
  </template>

  <ModalShell v-if="modal" :title="modalTitle" subtitle="性能流水线演示数据 · Mock 实现" wide @close="close">
    <p v-if="modalError" class="form-error" role="alert"><Icon name="alert" :size="17" />{{ modalError }}</p>
    <template v-if="modal.kind === 'edit'">
      <form id="perf-form" class="form-stack" @submit.prevent="saveDraft">
        <label>配置名称<input v-model="form.name" required maxlength="60" :disabled="busy" placeholder="例如：系统启动性能基线" /></label>
        <label>描述<textarea v-model="form.description" rows="2" maxlength="200" :disabled="busy" placeholder="说明采集场景与数据用途" /></label>
        <label>处理器架构<select v-model="form.arch" :disabled="busy"><option>ARM64</option><option>x86_64</option></select></label>
        <div class="form-stack">
          <div class="metric-editor-head"><span>指标标识</span><span>名称</span><span>单位</span><span>方向</span><span>基线</span><span>允许波动 %</span><span /></div>
          <div v-for="(metric, index) in form.metrics" :key="index" class="metric-editor-row">
            <input v-model="metric.key" required maxlength="32" :disabled="busy" placeholder="boot-time" class="mono" :aria-label="`第 ${index + 1} 行指标标识`" />
            <input v-model="metric.name" required maxlength="40" :disabled="busy" placeholder="开机耗时" :aria-label="`第 ${index + 1} 行指标名称`" />
            <input v-model="metric.unit" required maxlength="20" :disabled="busy" placeholder="ms" :aria-label="`第 ${index + 1} 行单位`" />
            <select v-model="metric.direction" :disabled="busy" :aria-label="`第 ${index + 1} 行方向`"><option value="lower">越低越好</option><option value="higher">越高越好</option></select>
            <input v-model="metric.baseline" required type="number" step="any" min="0" :disabled="busy" placeholder="18500" class="mono" :aria-label="`第 ${index + 1} 行基线`" />
            <input v-model="metric.threshold" required type="number" step="any" min="0" max="100" :disabled="busy" placeholder="5" class="mono" :aria-label="`第 ${index + 1} 行允许波动`" />
            <button type="button" class="icon-button" :disabled="busy || form.metrics.length <= 1" :aria-label="`删除第 ${index + 1} 行指标`" @click="removeMetric(index)"><Icon name="close" :size="14" /></button>
          </div>
          <div><button type="button" class="button secondary small" :disabled="busy || form.metrics.length >= 8" @click="addMetric"><Icon name="plus" :size="14" />添加指标（最多 8 项）</button></div>
        </div>
        <div class="info-banner"><Icon name="info" :size="18" /><p>偏离基线超过允许波动（坏方向）判定为回归，好方向超出 1% 记为优化；采集时按运行快照逐项判定。</p></div>
      </form>
    </template>
    <template v-else-if="modal.kind === 'start'">
      <form id="perf-start-form" class="form-stack" @submit.prevent="startRun">
        <label>性能配置<select v-model="startForm.pipelineId" :disabled="busy" required><option disabled value="">请选择已发布配置</option><option v-for="pipeline in pipelines.filter(p => p.status === 'published')" :key="pipeline.id" :value="pipeline.id">{{ pipeline.name }} · {{ pipeline.metrics.length }} 项指标</option></select></label>
        <label>采集环境（{{ pipelines.find(p => p.id === startForm.pipelineId)?.arch }} · 空闲）<select v-model="startForm.envId" :disabled="busy" required><option disabled value="">请选择匹配架构的空闲环境</option><option v-for="env in startEnvs" :key="env.id" :value="env.id">{{ env.name }} · {{ env.id }}</option></select></label>
        <label>被测版本<input v-model="startForm.version" required maxlength="40" :disabled="busy" placeholder="OS 24.03 RC2" /></label>
        <p v-if="startForm.pipelineId && !startEnvs.length" class="subtle-note">暂无匹配架构的空闲环境，可先在环境资源页释放或新增环境。</p>
        <div class="info-banner"><Icon name="info" :size="18" /><p>演示采集约十几秒完成，指标值由确定性噪声生成并与基线判定；结果会同步出现在「性能数据看板」。</p></div>
      </form>
    </template>
    <template v-else-if="modal.kind === 'run' && selectedRun">
      <div class="detail-summary"><div><strong class="mono">{{ selectedRun.id }}</strong><p>{{ selectedRun.pipelineName }} · {{ selectedRun.envName }}</p></div><StatusBadge :status="selectedRun.status" :label="selectedRun.status === 'success' ? '采集完成' : undefined" /></div>
      <div v-if="selectedRun.status === 'running'" class="run-progress"><div class="progress-track"><i :style="{ width: selectedRun.progress + '%' }" /></div><strong class="mono">{{ selectedRun.progress }}%</strong></div>
      <dl class="detail-grid"><div><dt>被测版本</dt><dd class="mono">{{ selectedRun.version }}</dd></div><div><dt>采集环境</dt><dd>{{ selectedRun.envName }} · <span class="mono">{{ selectedRun.envId }}</span></dd></div><div><dt>开始时间</dt><dd class="mono">{{ selectedRun.started }}</dd></div><div><dt>执行人</dt><dd>{{ selectedRun.user }}</dd></div></dl>
      <h3 class="section-title">指标结果</h3>
      <div v-if="(selectedRun.results || []).length" class="table-scroll"><table class="data-table"><thead><tr><th>指标</th><th>采集值</th><th>基线</th><th>偏差</th><th>判定</th></tr></thead><tbody><tr v-for="result in selectedRun.results" :key="result.key"><td><strong>{{ result.name }}</strong><small class="mono">{{ result.key }}</small></td><td class="mono">{{ result.value }} <small>{{ result.unit }}</small></td><td class="mono">{{ result.baseline }} {{ result.unit }}</td><td class="mono" :class="result.verdict === 'regress' ? 'color-red' : result.verdict === 'improve' ? 'color-green' : ''">{{ fmtDelta(result.deltaPct) }}</td><td><span class="badge" :class="`tone-${verdictMeta[result.verdict].tone}`">{{ verdictMeta[result.verdict].label }}</span><small class="subtle-note">{{ result.direction === 'lower' ? '越低越好' : '越高越好' }} · 允许 ±{{ result.threshold }}%</small></td></tr></tbody></table></div>
      <p v-else-if="selectedRun.status === 'running'" class="subtle-note">采集中，完成后按基线逐项生成判定结果。</p>
      <div v-else class="info-banner"><Icon name="alert" :size="18" /><p>{{ selectedRun.error || '采集未产生结果。' }}</p></div>
      <div v-if="selectedRun.status === 'running'" class="info-banner"><Icon name="pulse" :size="18" /><p>采集进度按演示节奏自动推进，也可手动推进或标记失败；数据仅保存在本浏览器。</p></div>
      <div v-else class="info-banner"><Icon name="info" :size="18" /><p>{{ (selectedRun.results || []).some(r => r.verdict === 'regress') ? '存在回归项；真实链路会关联 RDC 问题并触发修复后回归。' : '本次演示采集全部达标；回归判定不会自动创建问题。' }}</p></div>
      <div v-if="selectedRun.status === 'running'" class="action-stack"><button class="button danger" :disabled="busy" @click="markFailed">模拟采集失败</button><button class="button secondary" :disabled="busy" @click="advance"><Icon name="refresh" :size="16" />手动推进</button></div>
    </template>
    <template v-else-if="modal.kind === 'config' && pipelines.find(p => p.id === modal.id)">
      <div class="detail-summary"><div><strong>{{ pipelines.find(p => p.id === modal.id).name }}</strong><p class="mono">{{ pipelines.find(p => p.id === modal.id).id }} · {{ pipelines.find(p => p.id === modal.id).arch }}</p></div><StatusBadge :status="pipelines.find(p => p.id === modal.id).status" /></div>
      <div class="table-scroll"><table class="data-table"><thead><tr><th>指标</th><th>方向</th><th>基线</th><th>允许波动</th></tr></thead><tbody><tr v-for="metric in pipelines.find(p => p.id === modal.id).metrics" :key="metric.key"><td><strong>{{ metric.name }}</strong><small class="mono">{{ metric.key }}</small></td><td>{{ PERF_DIRECTIONS[metric.direction] }}</td><td class="mono">{{ metric.baseline }} {{ metric.unit }}</td><td class="mono">±{{ metric.threshold }}%</td></tr></tbody></table></div>
      <p class="subtle-note">创建人 {{ pipelines.find(p => p.id === modal.id).createdBy }} · {{ pipelines.find(p => p.id === modal.id).createdAt }}；演示配置不绑定脚本或采集命令。</p>
    </template>
    <template v-else-if="modal.kind === 'publish' && pipelines.find(p => p.id === modal.id)">
      <div class="notice-banner"><Icon name="check" :size="24" /><div><strong>{{ pipelines.find(p => p.id === modal.id).name }}</strong><p>发布后配置不可再修改；启动采集时按当前指标与基线快照判定。</p></div></div>
      <p class="subtle-note">发布属于管理员操作；草稿可随时继续编辑。</p>
    </template>
    <template #footer><span class="subtle-note"><Icon v-if="busy" name="refresh" :size="14" :class="{ spinning: busy }" />{{ busy ? '正在处理…' : '浏览器演示数据' }}</span><div class="modal-footer-actions">
      <button class="button secondary" :disabled="busy" @click="close">取消</button>
      <button v-if="modal.kind === 'edit'" class="button primary" type="submit" form="perf-form" :disabled="busy">保存草稿</button>
      <button v-if="modal.kind === 'start'" class="button primary" type="submit" form="perf-start-form" :disabled="busy || !startForm.pipelineId || !startForm.envId || !startForm.version.trim()">启动采集</button>
      <button v-if="modal.kind === 'publish'" class="button primary" :disabled="busy" @click="publish">确认发布</button>
    </div></template>
  </ModalShell>
  <Transition name="toast"><div v-if="toast" class="toast-message" role="status"><Icon name="check" :size="18" /><span>{{ toast }}</span></div></Transition>
</template>
