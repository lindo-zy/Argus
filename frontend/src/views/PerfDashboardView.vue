<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import Icon from '../components/Icon.vue'
import MetricCard from '../components/MetricCard.vue'
import StatusBadge from '../components/StatusBadge.vue'
import ModalShell from '../components/ModalShell.vue'
import MetricTrendChart from '../components/MetricTrendChart.vue'
import { getPerfStore, perfMetricSummary, perfVersionStats } from '../mock/perf.js'

// 性能数据看板（规划页 · Mock 实现）：聚合性能流水线演示 store（argus.perf.v1）的采集结果。
// 固定历史样本截至 2026-10-30，本地新启动的演示采集会追加进来；不连接真实采集代理。
const store = getPerfStore()
const snapshot = ref(null), loading = ref(true), loadError = ref(''), busy = ref(false), modalError = ref('')
const modal = ref(null)
const summaries = computed(() => snapshot.value ? perfMetricSummary(snapshot.value) : [])
const selectedKey = ref('')
const summary = computed(() => summaries.value.find(item => item.key === selectedKey.value) || summaries.value[0] || null)
const trendPoints = computed(() => (summary.value?.points || []).slice(-12).map(point => ({ label: String(point.started).slice(5, 10), value: point.value, deltaPct: point.deltaPct, verdict: point.verdict })))
const versionRows = computed(() => summary.value ? perfVersionStats(snapshot.value, summary.value) : [])
const runs = computed(() => snapshot.value?.runs || [])
const selectedRun = computed(() => runs.value.find(run => run.id === modal.value?.id))
const stats = computed(() => {
  const results = runs.value.filter(run => run.status === 'success').flatMap(run => run.results || [])
  const regress = results.filter(item => item.verdict === 'regress').length
  return {
    runs: runs.value.length,
    completed: runs.value.filter(run => run.status === 'success').length,
    metrics: summaries.value.length,
    regress,
    passRate: results.length ? ((results.length - regress) / results.length * 100).toFixed(1) : '—',
  }
})
const verdictMeta = { pass: { label: '达标', tone: 'green' }, improve: { label: '优化', tone: 'teal' }, regress: { label: '回归', tone: 'red' } }
const fmtDelta = value => `${value > 0 ? '+' : ''}${value}%`
async function load() {
  loading.value = true; loadError.value = ''
  try {
    const result = await store.load(); snapshot.value = result.snapshot
    if (!selectedKey.value && summaries.value.length) selectedKey.value = summaries.value[0].key
  } catch (error) { loadError.value = error.message || '看板数据加载失败。' } finally { loading.value = false }
}
let timer
onMounted(async () => {
  await load()
  timer = setInterval(async () => {
    const running = runs.value.filter(run => run.status === 'running')
    if (!running.length) return
    for (const run of running) { try { await store.tickPerfRun(run.id) } catch { /* 已结束 */ } }
    try { snapshot.value = (await store.load()).snapshot } catch { /* 下次再试 */ }
  }, 1500)
})
onBeforeUnmount(() => clearInterval(timer))
function open(kind, id = null) { modalError.value = ''; modal.value = { kind, id } }
function close() { if (!busy.value) modal.value = null }
</script>
<template>
  <div class="page-heading"><div><div class="eyebrow">PERFORMANCE INSIGHTS</div><h1>性能数据看板<span class="demo-tag">演示数据 · Mock 实现</span></h1><p>按指标聚合采集值、判定基线偏差与回归项，并按被测版本对比均值；数据来自性能流水线演示采集。</p></div></div>
  <div class="metrics-grid environment-metrics">
    <MetricCard label="采集运行" :value="stats.completed" :detail="`共 ${stats.runs} 条 · 含固定历史样本`" icon="pulse" />
    <MetricCard label="覆盖指标" :value="stats.metrics" detail="来自已发布与草稿配置" icon="chart" tone="teal" />
    <MetricCard label="回归项" :value="stats.regress" detail="超出基线允许波动" icon="alert" tone="red" />
    <MetricCard label="基线达标率" :value="stats.passRate" unit="%" detail="非回归结果 ÷ 全部结果" icon="check" tone="green" />
  </div>
  <div v-if="loading" class="loading-state" role="status"><span class="spinner" /><p>正在聚合性能演示数据…</p></div>
  <div v-else-if="loadError" class="empty-state panel" role="alert"><Icon name="alert" :size="34" /><p>{{ loadError }}</p><button class="button secondary small" @click="load">重新加载</button></div>
  <template v-else>
    <div class="perf-board-grid">
      <section class="panel chart-panel" aria-label="指标趋势">
        <div class="panel-heading"><h2>指标趋势</h2><label class="board-selector select-label">指标<select v-model="selectedKey" aria-label="选择查看的指标"><option v-for="item in summaries" :key="item.key" :value="item.key">{{ item.name }}（{{ item.unit }}）</option></select></label></div>
        <MetricTrendChart v-if="summary" :points="trendPoints" :baseline="summary.baseline" :unit="summary.unit" />
        <div v-else class="empty-state"><Icon name="chart" :size="32" /><p>暂无可展示的指标；先在性能流水线完成一次采集。</p></div>
      </section>
      <section class="panel" style="padding:18px" aria-label="指标最新判定">
        <div class="panel-heading"><h2>最新判定</h2><span class="subtle-note">按最近采集</span></div>
        <div v-for="item in summaries" :key="item.key" class="failure-row" style="grid-template-columns: 1fr auto auto">
          <span><i class="dot" :class="item.latest.verdict === 'regress' ? 'red' : item.latest.verdict === 'improve' ? 'green' : 'teal'" /><strong class="plain-title" style="margin-left:6px">{{ item.name }}</strong><small class="mono" style="display:block;color:var(--faint)">{{ item.latest.value }} {{ item.unit }} · {{ item.regressCount }} 次回归 / {{ item.runCount }} 次采集</small></span>
          <span class="mono" :class="item.latest.verdict === 'regress' ? 'color-red' : item.latest.verdict === 'improve' ? 'color-green' : ''">{{ fmtDelta(item.latest.deltaPct) }}</span>
          <span class="badge" :class="`tone-${verdictMeta[item.latest.verdict].tone}`">{{ verdictMeta[item.latest.verdict].label }}</span>
        </div>
        <div v-if="!summaries.length" class="empty-state"><Icon name="list" :size="30" /><p>暂无判定数据</p></div>
      </section>
    </div>
    <section class="panel workspace-panel" aria-label="版本对比">
      <div class="panel-heading quality-heading"><h2>版本对比<span v-if="summary" class="subtle-note" style="margin-left:10px">{{ summary.name }} · 基线 {{ summary.baseline }} {{ summary.unit }} · {{ summary.direction === 'lower' ? '越低越好' : '越高越好' }}</span></h2><span class="subtle-note">成功采集按版本聚合</span></div>
      <div v-if="versionRows.length" class="table-scroll"><table class="data-table"><thead><tr><th>被测版本</th><th>采集次数</th><th>平均值</th><th>最优值</th><th>均值偏差</th></tr></thead><tbody><tr v-for="row in versionRows" :key="row.version"><td class="mono">{{ row.version }}</td><td>{{ row.count }} 次</td><td class="mono">{{ row.avg }} {{ summary.unit }}</td><td class="mono">{{ row.best }} {{ summary.unit }}</td><td class="mono">{{ fmtDelta(row.deltaPct) }}</td></tr></tbody></table></div>
      <div v-else class="empty-state"><Icon name="chart" :size="30" /><p>该指标暂无按版本聚合的数据</p></div>
    </section>
    <section class="panel workspace-panel" aria-label="采集运行明细">
      <div class="panel-heading quality-heading"><h2>采集运行明细</h2><span class="subtle-note">{{ runs.length }} 条 · 最新在前</span></div>
      <div class="table-scroll"><table class="data-table"><thead><tr><th>运行 / 配置</th><th>环境</th><th>被测版本</th><th>状态</th><th>回归项</th><th>时间</th><th>操作</th></tr></thead><tbody><tr v-for="run in runs" :key="run.id"><td><button class="inline-link mono" @click="open('run', run.id)">{{ run.id }}</button><small>{{ run.pipelineName }}</small></td><td>{{ run.envName }}<small class="mono">{{ run.envId }}</small></td><td class="mono">{{ run.version }}</td><td><StatusBadge :status="run.status" :label="run.status === 'success' ? '采集完成' : undefined" /></td><td><span class="badge" :class="(run.results || []).some(item => item.verdict === 'regress') ? 'tone-red' : 'tone-muted'">{{ (run.results || []).filter(item => item.verdict === 'regress').length }} 项</span></td><td class="mono">{{ run.started }}<small v-if="run.ended" class="mono">→ {{ run.ended }}</small></td><td><button class="text-button" @click="open('run', run.id)">指标明细<Icon name="chevron" :size="13" /></button></td></tr></tbody></table></div>
    </section>
    <div class="page-footnote"><Icon name="info" :size="15" /><span>性能数据看板为规划页的 Mock 实现：历史样本固定截至 2026-10-30，本地演示采集实时追加；指标口径（单位、方向、基线、允许波动）以性能流水线配置为准，当前不连接真实采集代理或数据仓库。</span></div>
  </template>

  <ModalShell v-if="modal?.kind === 'run' && selectedRun" :title="`采集明细 · ${selectedRun.id}`" subtitle="性能数据看板 · Mock 演示数据" wide @close="close">
    <div class="detail-summary"><div><strong class="mono">{{ selectedRun.id }}</strong><p>{{ selectedRun.pipelineName }} · {{ selectedRun.envName }} · {{ selectedRun.version }}</p></div><StatusBadge :status="selectedRun.status" :label="selectedRun.status === 'success' ? '采集完成' : undefined" /></div>
    <div v-if="(selectedRun.results || []).length" class="table-scroll"><table class="data-table"><thead><tr><th>指标</th><th>采集值</th><th>基线</th><th>偏差</th><th>判定</th></tr></thead><tbody><tr v-for="result in selectedRun.results" :key="result.key"><td><strong>{{ result.name }}</strong><small class="mono">{{ result.key }}</small></td><td class="mono">{{ result.value }} <small>{{ result.unit }}</small></td><td class="mono">{{ result.baseline }} {{ result.unit }}</td><td class="mono" :class="result.verdict === 'regress' ? 'color-red' : result.verdict === 'improve' ? 'color-green' : ''">{{ fmtDelta(result.deltaPct) }}</td><td><span class="badge" :class="`tone-${verdictMeta[result.verdict].tone}`">{{ verdictMeta[result.verdict].label }}</span></td></tr></tbody></table></div>
    <div v-else class="info-banner"><Icon name="alert" :size="18" /><p>{{ selectedRun.error || '该运行没有指标结果。' }}</p></div>
    <template #footer><span class="subtle-note">浏览器演示数据</span><div class="modal-footer-actions"><button class="button secondary" @click="close">关闭</button></div></template>
  </ModalShell>
</template>
