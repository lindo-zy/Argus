<script setup>
import { computed } from 'vue'
const props = defineProps({ devices: Array })
const groups = computed(() => [
  { label: '物理机', devices: props.devices.filter(d => d.type === 'physical') },
  { label: '虚拟机', devices: props.devices.filter(d => d.type === 'virtual') },
].map(group => ({ ...group, segments: [
  { label: '空闲', color: 'green', count: group.devices.filter(d => d.status === 'available').length },
  { label: '占用', color: 'blue', count: group.devices.filter(d => ['running', 'leased'].includes(d.status)).length },
  { label: '待处理', color: 'amber', count: group.devices.filter(d => ['maintenance', 'reconciling', 'cleaning', 'checking'].includes(d.status)).length },
  { label: '隔离', color: 'red', count: group.devices.filter(d => d.status === 'isolated').length },
] })))
</script>
<template><div class="legend compact"><span v-for="(label, i) in ['空闲', '占用', '待处理', '隔离']" :key="label"><i class="dot" :class="['green','blue','amber','red'][i]" />{{ label }}</span></div><div v-for="group in groups" :key="group.label" class="resource-bar-group"><div class="resource-bar-heading"><span>{{ group.label }}</span><span class="mono">{{ group.devices.length }} 台</span></div><div class="stacked-bar" :aria-label="`${group.label}资源分布`"><i v-for="segment in group.segments" :key="segment.label" :class="`bg-${segment.color}`" :style="{ width: segment.count / (group.devices.length || 1) * 100 + '%' }"><title>{{ segment.label }} {{ segment.count }} 台</title></i></div><div class="resource-bar-values"><span v-for="segment in group.segments" :key="segment.label" :class="`color-${segment.color}`">{{ segment.count }}<small> 台</small></span></div></div></template>
