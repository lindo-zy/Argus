<script setup>
import { computed } from 'vue'
const props = defineProps({ points: { type: Array, required: true } })
const max = computed(() => Math.ceil(Math.max(...props.points.map(p => p.runs)) / 5) * 5)
const x = i => 54 + i * (534 / Math.max(1, props.points.length - 1))
const y = rate => 178 - (rate - 80) / 20 * 144
const line = computed(() => props.points.map((p, i) => `${x(i)},${y(p.rate)}`).join(' '))
</script>
<template>
  <div class="chart-legend"><span><i class="dot blue" />执行次数</span><span><i class="dot teal" />通过率</span></div>
  <svg class="trend-chart" viewBox="0 0 650 222" role="img" aria-label="测试执行趋势：蓝色柱形为执行次数，青色折线为通过率">
    <g v-for="n in 5" :key="n"><line x1="36" x2="610" :y1="34 + (n - 1) * 36" :y2="34 + (n - 1) * 36" class="chart-grid" /><text x="27" :y="38 + (n - 1) * 36" text-anchor="end">{{ Math.round(max * (5 - n) / 4) }}</text><text x="620" :y="38 + (n - 1) * 36">{{ 100 - (n - 1) * 5 }}%</text></g>
    <g v-for="(p, i) in points" :key="p.label"><rect :x="x(i) - 18" :y="178 - p.runs / max * 144" width="36" :height="p.runs / max * 144" rx="4" class="chart-bar"><title>{{ p.label }} · {{ p.runs }} 次执行 · 通过率 {{ p.rate.toFixed(1) }}%</title></rect><text :x="x(i)" y="206" text-anchor="middle">{{ p.label }}</text></g>
    <polyline :points="line" fill="none" class="chart-line" stroke-width="2.5" stroke-linejoin="round" />
    <circle v-for="(p, i) in points" :key="`p${i}`" :cx="x(i)" :cy="y(p.rate)" r="4" class="chart-point"><title>{{ p.label }} · {{ p.rate.toFixed(1) }}%</title></circle>
  </svg>
</template>
