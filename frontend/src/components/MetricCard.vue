<script setup>
import Icon from './Icon.vue'
defineProps({ label: String, value: [String, Number], unit: String, detail: String, icon: String, tone: { type: String, default: 'blue' }, spark: Boolean })
</script>
<template>
  <article class="metric-card panel" :class="`tone-${tone}`">
    <span class="metric-icon"><Icon :name="icon" :size="23" /></span>
    <div class="metric-copy"><span class="metric-label">{{ label }}</span><div class="metric-value">{{ value }}<small v-if="unit">{{ unit }}</small></div><span class="metric-detail">{{ detail }}</span></div>
    <svg v-if="spark" class="sparkline" viewBox="0 0 100 42" aria-hidden="true"><path d="M2 35 13 30 24 33 35 20 46 25 57 17 68 21 79 12 90 14 98 3" fill="none" stroke="currentColor" stroke-width="2" /></svg>
  </article>
</template>
