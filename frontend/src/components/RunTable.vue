<script setup>
import StatusBadge from './StatusBadge.vue'
import Icon from './Icon.vue'
defineProps({ runs: Array, compact: Boolean })
defineEmits(['select'])
const external = run => !!run.executionMode && run.executionMode !== 'demo'
const sourceLabel = run => run.executionMode === 'jenkins' ? 'Jenkins' : run.executionMode === 'mock' ? 'Jenkins HTTP Mock' : '演示样本'
</script>
<template>
  <div class="table-scroll"><table class="data-table"><thead><tr><th>任务 / 配置</th><th>环境</th><th>状态</th><th>通过 / 总数</th><th>耗时</th><th>证据</th><th><span class="sr-only">详情</span></th></tr></thead><tbody><tr v-for="run in runs" :key="run.id"><td><button class="inline-link mono" @click="$emit('select', run)">{{ run.id }}</button><small v-if="!compact">{{ run.name }}</small><small>{{ sourceLabel(run) }}</small></td><td class="mono">{{ run.envId }}</td><td><StatusBadge :status="run.status" /></td><td class="mono">{{ external(run) && !run.total ? '未回传' : ['success', 'failed', 'cleaning'].includes(run.status) ? `${run.passed} / ${run.total}` : '—' }}</td><td class="mono">{{ run.duration }} min</td><td><button class="inline-link mono" :aria-label="`查看 ${run.id} 执行证据`" @click="$emit('select', run)">{{ external(run) ? `${run.evidence} 条引用` : `${run.evidence} / 5` }}</button></td><td><button class="icon-button" :aria-label="`查看任务 ${run.id}`" @click="$emit('select', run)"><Icon name="chevron" :size="16" /></button></td></tr></tbody></table><div v-if="!runs.length" class="empty-state"><Icon name="search" :size="32" /><p>没有匹配的执行记录</p><span>试试其他状态或搜索词</span></div></div>
</template>
