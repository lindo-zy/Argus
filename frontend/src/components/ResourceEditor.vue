<script setup>
import { computed, ref, watch } from 'vue'
import Icon from './Icon.vue'
import StatusBadge from './StatusBadge.vue'
const props = defineProps({ kind: String, entity: Object, devices: Array, busy: Boolean, initialDraft: Object })
const emit = defineEmits(['save', 'add-device'])
const isEnvironment = computed(() => props.kind === 'environment')
const initial = props.entity || props.initialDraft
const draft = ref({
  name: initial?.name || '', arch: initial?.arch || 'ARM64', type: initial?.type || 'physical',
  version: initial?.version || '', description: initial?.description || '', location: initial?.location || '',
  deviceIds: [...(initial?.deviceIds || [])], cpu: initial?.cpu || '', memory: initial?.memory || '', ip: initial?.ip || '',
})
const deviceQuery = ref('')
const candidates = computed(() => props.devices.filter(d => d.lifecycle !== 'retired' && d.arch === draft.value.arch && d.type === draft.value.type && `${d.name} ${d.id}`.toLowerCase().includes(deviceQuery.value.trim().toLowerCase())))
const unavailable = device => ['leased', 'running', 'maintenance', 'checking', 'cleaning', 'reconciling'].includes(device.status)
watch(() => [draft.value.arch, draft.value.type], () => {
  draft.value.deviceIds = draft.value.deviceIds.filter(id => props.devices.some(d => d.id === id && d.arch === draft.value.arch && d.type === draft.value.type))
})
function submit() { emit('save', { ...draft.value, ...(props.entity ? { id: props.entity.id } : {}) }) }
</script>

<template>
  <form id="resource-form" class="form-stack" @submit.prevent="submit"><fieldset class="resource-form-fields" :disabled="busy">
    <div class="editor-intro"><Icon :name="isEnvironment ? 'box' : 'monitor'" :size="20" /><p>用户自助维护，保存后立即更新资源池，无需管理员审批。</p></div>
    <label>{{ isEnvironment ? '环境名称' : '设备名称' }}<input v-model="draft.name" required maxlength="60" :placeholder="isEnvironment ? '例如：ARM64 网络联调环境' : '例如：arm-node-59'" /></label>
    <div class="editor-field-grid"><label>处理器架构<select v-model="draft.arch"><option>ARM64</option><option>x86_64</option></select></label><label>资源类型<select v-model="draft.type"><option value="physical">物理机</option><option value="virtual">虚拟机</option></select></label></div>
    <template v-if="isEnvironment"><label>用途说明<textarea v-model="draft.description" maxlength="200" rows="2" placeholder="说明环境用途，便于其他用户选择" /></label><div class="editor-field-grid"><label>系统版本（可选）<input v-model="draft.version" maxlength="80" placeholder="例如：OS 24.03 RC2" /></label><label>部署位置（可选）<input v-model="draft.location" maxlength="100" placeholder="例如：实验室 A" /></label></div>
      <fieldset class="device-selection"><legend>关联设备<span class="subtle-note">已选 {{ draft.deviceIds.length }} 台</span></legend><label class="search-field"><Icon name="search" :size="16" /><input v-model="deviceQuery" aria-label="搜索关联设备" placeholder="搜索设备名称或 ID" /></label><div class="device-choice-list"><label v-for="device in candidates" :key="device.id" class="device-choice"><input v-model="draft.deviceIds" type="checkbox" :value="device.id" :disabled="unavailable(device) && !draft.deviceIds.includes(device.id)" :aria-label="`关联 ${device.name}`" /><span><strong>{{ device.name }}</strong><small class="mono">{{ device.id }} · {{ device.cpu || '未填写规格' }}</small></span><StatusBadge :status="device.status" :label="device.status === 'available' ? '空闲' : undefined" /></label><p v-if="!candidates.length" class="subtle-note device-choice-empty">没有匹配架构与类型的设备，请先新增设备或调整筛选。</p></div></fieldset>
      <button v-if="!entity" class="text-button" type="button" @click="emit('add-device', { ...draft })"><Icon name="plus" :size="15" />先新增设备</button><p class="subtle-note">可以复用已有设备组成新环境；实际借用仍按设备占用情况校验。</p>
    </template>
    <template v-else><div class="editor-field-grid"><label>CPU 规格（可选）<input v-model="draft.cpu" maxlength="100" placeholder="例如：Kunpeng 920 · 32 核" /></label><label>内存规格（可选）<input v-model="draft.memory" maxlength="60" placeholder="例如：128 GB" /></label></div><label>设备地址（可选）<input v-model="draft.ip" maxlength="100" placeholder="例如：10.24.1.11" /></label><label>设备位置（可选）<input v-model="draft.location" maxlength="100" placeholder="例如：实验室 A / 机柜 1" /></label><p class="subtle-note">新增的演示设备以空闲状态加入资源池，不连接真实设备。被环境引用的设备修改架构或类型前，需先调整关联。</p></template>
  </fieldset></form>
</template>
