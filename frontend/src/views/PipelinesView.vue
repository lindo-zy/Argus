<script setup>
import { computed, ref } from 'vue'
import Icon from '../components/Icon.vue'
import MetricCard from '../components/MetricCard.vue'
import StatusBadge from '../components/StatusBadge.vue'
const props = defineProps({ snapshot: Object, envs: Array })
const emit = defineEmits(['pipeline'])
const query = ref(''), state = ref('all'), arch = ref('all')
// 已发布配置按架构匹配当前空闲环境；共享设备被占用时环境不会出现在可借列表里。
const idleEnvs = pipeline => props.envs.filter(e => e.status === 'available' && e.arch === pipeline.arch)
const counts = computed(() => ({
  all: props.snapshot.pipelines.length,
  published: props.snapshot.pipelines.filter(p => p.status === 'published').length,
  draft: props.snapshot.pipelines.filter(p => p.status === 'draft').length,
  ready: props.snapshot.pipelines.filter(p => p.status === 'published' && idleEnvs(p).length).length,
}))
const filtered = computed(() => props.snapshot.pipelines.filter(p =>
  `${p.id} ${p.name} ${p.description} ${p.job}`.toLowerCase().includes(query.value.trim().toLowerCase())
  && (state.value === 'all' || state.value === p.status)
  && (arch.value === 'all' || arch.value === p.arch)))
const startHint = pipeline => pipeline.status !== 'published'
  ? '草稿不能启动任务，发布治理为后续独立设计'
  : idleEnvs(pipeline).length ? undefined : '暂无匹配架构的空闲环境'
function clear() { query.value = ''; state.value = 'all'; arch.value = 'all' }
</script>
<template>
  <div class="page-heading"><div><div class="eyebrow">PIPELINE ORCHESTRATION</div><h1>标准流水线<span class="demo-tag">演示数据</span></h1><p>使用已发布的声明式配置，关联受控执行任务；升级与性能流水线建设中。</p></div><div class="heading-actions"><span class="badge tone-blue">XML 声明式配置</span></div></div>
  <div class="metrics-grid environment-metrics"><MetricCard label="流水线配置" :value="counts.all" detail="声明式 XML · 只读预览" icon="flow" spark /><MetricCard label="已发布" :value="counts.published" detail="发布版本不可变" icon="check" tone="green" spark /><MetricCard label="草稿" :value="counts.draft" detail="不能启动任务" icon="code" tone="amber" spark /><MetricCard label="可立即启动" :value="counts.ready" detail="已发布且匹配空闲环境" icon="play" spark /></div>
  <section class="panel workspace-panel" aria-label="流水线配置列表">
    <div class="workspace-toolbar"><label class="search-field"><Icon name="search" :size="18" /><input v-model="query" aria-label="搜索流水线配置" placeholder="搜索名称、ID 或 Job" /><button v-if="query" class="icon-button" aria-label="清空搜索" @click="query = ''"><Icon name="close" :size="14" /></button></label><label class="select-label">状态<select v-model="state" aria-label="筛选配置状态"><option value="all">全部状态</option><option value="published">已发布</option><option value="draft">草稿</option></select></label><label class="select-label">架构<select v-model="arch" aria-label="筛选架构"><option value="all">全部架构</option><option>ARM64</option><option>x86_64</option></select></label><span class="subtle-note toolbar-end">{{ filtered.length }} 个配置</span></div>
    <div v-if="filtered.length" class="pipeline-list"><article v-for="pipeline in filtered" :key="pipeline.id" class="pipeline-config"><div class="resource-icon"><Icon name="flow" :size="26" /></div><div><h3>{{ pipeline.name }}<span class="version-tag mono">{{ pipeline.version }}</span></h3><p>{{ pipeline.description }}</p><div class="metadata"><span class="mono">{{ pipeline.id }}</span><span>· {{ pipeline.arch }}</span><span>· Job <span class="mono">{{ pipeline.job || '未登记' }}</span></span><span>· 更新于 {{ pipeline.date || '—' }}</span></div><div class="metadata"><template v-if="pipeline.status === 'published'"><span class="badge" :class="idleEnvs(pipeline).length ? 'tone-blue' : 'tone-muted'">{{ idleEnvs(pipeline).length ? `匹配 ${idleEnvs(pipeline).length} 个空闲环境` : '暂无匹配空闲环境' }}</span></template><span v-else class="badge tone-muted">草稿不能启动任务</span></div></div><StatusBadge :status="pipeline.status" /><div class="table-actions"><button class="button secondary small" @click="emit('pipeline', pipeline)">查看配置</button><button class="button primary small" :disabled="startHint(pipeline) !== undefined" :title="startHint(pipeline)" @click="emit('pipeline', pipeline)">启动任务</button></div></article></div>
    <div v-else class="empty-state"><Icon name="search" :size="38" /><h3>没有找到匹配的配置</h3><p>调整搜索词或筛选条件，查看其他流水线配置。</p><button class="button secondary small" @click="clear">清空筛选</button></div>
    <div class="info-banner"><Icon name="info" :size="18" /><p>XML 为只读声明式配置预览；启动任务仅在本地创建演示运行并独占环境设备，不连接 Jenkins，也不执行脚本。</p></div>
  </section>
  <div class="page-footnote"><Icon name="info" :size="15" /><span>只有已发布配置可以启动任务；草稿与发布治理为后续独立设计，当前不提供配置编辑或发布入口。任务启动后的终态确认与清理释放见「测试执行记录」。</span></div>
</template>
