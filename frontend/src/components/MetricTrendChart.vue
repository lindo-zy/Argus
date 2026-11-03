<script setup>
import { computed } from 'vue'
import Icon from './Icon.vue'
// 指标趋势折线图：复用平台 SVG 图表样式（无图表库）；points 携带 verdict 时按判定着色。
const props = defineProps({
  points: { type: Array, required: true }, // [{ label, value, verdict? }]
  baseline: { type: Number, default: null },
  unit: { type: String, default: '' },
})
const W = 650, H = 222, TOP = 18, BOTTOM = 30
const values = computed(() => props.points.map(p => p.value).concat(props.baseline == null ? [] : [props.baseline]))
const bounds = computed(() => {
  const min = Math.min(...values.value), max = Math.max(...values.value)
  const span = max - min || Math.abs(max) * 0.1 || 1
  return { lo: min - span * 0.18, hi: max + span * 0.18 }
})
const x = i => 56 + i * (560 / Math.max(1, props.points.length - 1))
const y = value => TOP + (1 - (value - bounds.value.lo) / (bounds.value.hi - bounds.value.lo)) * (H - TOP - BOTTOM)
const line = computed(() => props.points.map((p, i) => `${x(i)},${y(p.value)}`).join(' '))
const gridLines = computed(() => Array.from({ length: 4 }, (_, i) => {
  const value = bounds.value.lo + (bounds.value.hi - bounds.value.lo) * (i / 3)
  return { y: y(value), value }
}))
const fmt = value => Math.abs(value) >= 100 ? Math.round(value).toLocaleString('zh-CN') : Number(value.toFixed(2)).toString()
</script>
<template>
  <div v-if="points.length" class="chart-legend"><span><i class="dot teal" />采集值</span><span v-if="baseline != null"><i class="chart-baseline-legend" />基线 {{ fmt(baseline) }}{{ unit }}</span></div>
  <svg class="trend-chart" viewBox="0 0 650 222" role="img" aria-label="指标趋势：折线为各次采集值，虚线为基线参考">
    <g v-for="grid in gridLines" :key="grid.y"><line x1="46" x2="622" :y1="grid.y" :y2="grid.y" class="chart-grid" /><text x="40" :y="grid.y + 3" text-anchor="end">{{ fmt(grid.value) }}</text></g>
    <line v-if="baseline != null && points.length > 1" x1="46" x2="622" :y1="y(baseline)" :y2="y(baseline)" class="chart-baseline" />
    <text v-if="baseline != null && points.length > 1" x="620" :y="y(baseline) - 5" text-anchor="end" class="chart-baseline-text">基线</text>
    <polyline :points="line" fill="none" class="chart-line" stroke-width="2.5" stroke-linejoin="round" />
    <circle v-for="(p, i) in points" :key="i" :cx="x(i)" :cy="y(p.value)" r="4.2" :class="['chart-point', p.verdict]">
      <title>{{ p.label }} · {{ fmt(p.value) }}{{ unit }} · 偏差 {{ p.deltaPct > 0 ? '+' : '' }}{{ p.deltaPct }}%</title>
    </circle>
    <text v-for="(p, i) in points" :key="`t${i}`" :x="x(i)" y="214" text-anchor="middle">{{ p.label }}</text>
  </svg>
  <div v-if="!points.length" class="empty-state"><Icon name="chart" :size="32" /><p>暂无成功采集数据</p></div>
</template>
