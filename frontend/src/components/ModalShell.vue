<script setup>
import { onMounted, onBeforeUnmount, ref } from 'vue'
import Icon from './Icon.vue'
defineProps({ title: String, subtitle: String, wide: Boolean, drawer: Boolean })
const emit = defineEmits(['close'])
const dialog = ref(null)
let previousFocus
function backdrop(event) { if (event.target === dialog.value) emit('close') }
onMounted(() => { previousFocus = document.activeElement; dialog.value.showModal() })
onBeforeUnmount(() => { dialog.value?.close(); previousFocus?.focus?.() })
</script>
<template>
  <dialog ref="dialog" class="modal-shell" :class="{ wide, drawer }" aria-labelledby="modal-title" @cancel.prevent="emit('close')" @click="backdrop">
    <div class="modal-content"><header class="modal-header"><div><h2 id="modal-title">{{ title }}</h2><p v-if="subtitle">{{ subtitle }}</p></div><button class="icon-button" aria-label="关闭弹窗" @click="emit('close')"><Icon name="close" /></button></header><div class="modal-body"><slot /></div><footer v-if="$slots.footer" class="modal-footer"><slot name="footer" /></footer></div>
  </dialog>
</template>
