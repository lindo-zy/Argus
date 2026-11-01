<script setup>
import { computed } from 'vue'
import Icon from './Icon.vue'
import StatusBadge from './StatusBadge.vue'
const props = defineProps({ env: Object })
const emit = defineEmits(['details', 'borrow', 'run', 'lease', 'maintenance'])
const extraTags = computed(() => (props.env.tags || []).filter(t => t !== props.env.arch && t !== '物理机' && t !== '虚拟机'))
</script>
<template>
  <article class="environment-card">
    <div class="environment-head"><div class="resource-icon"><Icon :name="env.type === 'virtual' ? 'monitor' : 'server'" :size="27" /></div><div class="environment-title"><h3><button class="plain-title" @click="emit('details', env)">{{ env.name }}</button></h3><span class="mono">{{ env.id }}</span></div><StatusBadge :status="env.status" /></div>
    <div class="environment-body"><p class="metadata">{{ env.arch }}<span>·</span>{{ env.type === 'physical' ? '物理机' : '虚拟机' }}<span>·</span>{{ env.deviceIds.length }} 台设备<span v-if="env.owner">·</span><span v-if="env.owner">{{ env.owner }}</span></p><p v-if="extraTags.length" class="env-tags"><span v-for="tag in extraTags" :key="tag" class="badge tone-muted">{{ tag }}</span></p><p><Icon name="monitor" :size="15" /><span>IP：<span class="mono">{{ env.ip || '未记录' }}</span></span></p><p v-if="env.type === 'virtual'"><Icon name="flow" :size="15" /><span>宿主机：<span class="mono">{{ env.host || '未记录' }}</span></span></p><p><Icon name="settings" :size="15" /><span>OS 版本：{{ env.version || '未记录' }}</span><span v-if="env.shared" class="shared-label"><Icon name="link" :size="12" />共享设备</span></p>
      <template v-if="env.run"><p><Icon name="play" :size="15" /><span>当前任务：<button class="inline-link mono" @click="emit('run', env.run)">{{ env.run.id }}</button></span></p><div class="progress-row"><div class="progress-track"><i :style="{ width: env.run.progress + '%' }" /></div><span class="mono">{{ env.run.progress }}%</span></div></template>
      <p v-else-if="env.lease"><Icon name="user" :size="15" /><span>{{ env.lease.user }} · {{ env.lease.purpose }}</span></p>
      <p v-else-if="env.status === 'available'"><Icon name="pulse" :size="15" /><span>最近健康检查：{{ env.health.includes(' ') ? env.health.slice(11) : env.health }}</span></p>
      <p v-else class="environment-warning"><Icon name="alert" :size="15" /><span>{{ env.shared ? '共享设备不可用，关联环境暂不可借' : '资源已锁定，复核通过后恢复可借' }}</span></p>
    </div>
    <footer class="environment-footer"><button class="text-button" @click="emit('details', env)">查看详情<Icon name="chevron" :size="14" /></button><button v-if="env.status === 'available'" class="button primary small" @click="emit('borrow', env)">申请借用</button><button v-else-if="env.run" class="button secondary small" @click="emit('run', env.run)">查看执行</button><button v-else-if="env.lease" class="button secondary small" @click="emit('lease', env.lease)">管理借用</button><button v-else-if="['maintenance', 'isolated'].includes(env.status)" class="button secondary small" @click="emit('maintenance', env)">查看维护</button><button v-else class="button secondary small" @click="emit('details', env)">查看占用</button></footer>
  </article>
</template>
