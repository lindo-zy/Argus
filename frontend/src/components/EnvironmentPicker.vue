<script setup>
import { computed, ref } from 'vue'
import Icon from './Icon.vue'
import StatusBadge from './StatusBadge.vue'
const props = defineProps({ envs: { type: Array, required: true }, modelValue: { type: Array, default: () => [] }, disabled: Boolean })
const emit = defineEmits(['update:modelValue'])
const query = ref(''), availableOnly = ref(true)
const filtered = computed(() => props.envs.filter(env =>
  (!availableOnly.value || env.status === 'available' || props.modelValue.includes(env.id)) &&
  `${env.name} ${env.id} ${env.arch}`.toLowerCase().includes(query.value.trim().toLowerCase()),
))
const selectable = computed(() => filtered.value.filter(env => env.status === 'available'))
const selected = computed(() => props.envs.filter(env => props.modelValue.includes(env.id)))
const deviceCount = computed(() => new Set(selected.value.flatMap(env => env.deviceIds)).size)
const sharedCount = computed(() => selected.value.reduce((sum, env) => sum + env.deviceIds.length, 0) - deviceCount.value)
const unavailableCount = computed(() => selected.value.filter(env => env.status !== 'available').length)
const missingCount = computed(() => props.modelValue.filter(id => !props.envs.some(env => env.id === id)).length)
function toggle(envId, checked) { emit('update:modelValue', checked ? [...new Set([...props.modelValue, envId])] : props.modelValue.filter(id => id !== envId)) }
function selectVisible() { emit('update:modelValue', [...new Set([...props.modelValue, ...selectable.value.map(env => env.id)])]) }
</script>

<template>
  <fieldset class="borrow-picker" :disabled="disabled"><legend>借用环境<span>可多选</span></legend>
    <div class="borrow-picker-tools"><label class="search-field"><Icon name="search" :size="16" /><input v-model="query" aria-label="搜索申请环境" placeholder="搜索环境名称、ID 或架构" /></label><label class="borrow-available-filter"><input v-model="availableOnly" type="checkbox" />只看可借用</label></div>
    <div class="borrow-picker-actions"><span>找到 {{ filtered.length }} 个环境</span><button class="text-button" type="button" :disabled="!selectable.length || disabled" @click="selectVisible">全选当前空闲环境</button><button class="text-button" type="button" :disabled="!modelValue.length || disabled" @click="emit('update:modelValue', [])">清空已选</button></div>
    <div class="borrow-options"><label v-for="env in filtered" :key="env.id" class="borrow-option" :class="{ selected: modelValue.includes(env.id), unavailable: env.status !== 'available' }"><input type="checkbox" :checked="modelValue.includes(env.id)" :disabled="disabled" :aria-label="`选择 ${env.name}`" @change="toggle(env.id, $event.target.checked)" /><span class="borrow-option-copy"><strong>{{ env.name }}</strong><small class="mono">{{ env.id }}</small><small>{{ env.arch }} · {{ env.type === 'physical' ? '物理机' : '虚拟机' }} · {{ env.deviceIds.length }} 台设备</small><small v-if="env.status !== 'available'" class="color-amber">当前不可借，提交时自动跳过</small></span><StatusBadge :status="env.status" /></label><div v-if="!filtered.length" class="borrow-picker-empty"><Icon name="search" :size="24" /><p>没有匹配的环境，试试其他搜索条件</p></div></div>
    <div class="borrow-selection-summary" role="status"><Icon name="box" :size="17" /><span>已选 <strong>{{ modelValue.length }}</strong> 个环境 · 共 <strong>{{ deviceCount }}</strong> 台设备</span><small v-if="sharedCount">共享设备已去重</small></div>
    <p class="subtle-note borrow-batch-hint">空闲环境直接借用，无需审批。{{ missingCount ? `${missingCount} 个环境已移出资源池，将自动跳过。` : '' }}{{ unavailableCount ? `当前 ${unavailableCount} 个非空闲环境将跳过，` : '' }}提交时逐项检查，可借环境不受其他环境影响。</p>
  </fieldset>
</template>
