<script setup>
import { computed } from 'vue'
import { statusMeta } from '../mock/seed.js'
const props = defineProps({ status: String, label: String })
const meta = computed(() => statusMeta[props.status] || { label: props.status, tone: 'muted' })
</script>
<template><span class="badge" :class="`tone-${meta.tone}`"><span class="status-dot" />{{ label || meta.label }}</span></template>
