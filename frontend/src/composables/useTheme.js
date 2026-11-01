import { computed, onBeforeUnmount, ref, watch } from 'vue'

export function useTheme() {
  const media = window.matchMedia('(prefers-color-scheme: dark)')
  const systemDark = ref(media.matches)
  let saved = 'system'
  try { saved = localStorage.getItem('argus.theme') || 'system' } catch { /* Private mode can deny storage. */ }
  const preference = ref(['light', 'dark', 'system'].includes(saved) ? saved : 'system')
  const presentation = ref(false)
  const resolved = computed(() => presentation.value ? 'dark' : preference.value === 'system' ? (systemDark.value ? 'dark' : 'light') : preference.value)
  const onChange = event => { systemDark.value = event.matches }
  media.addEventListener('change', onChange)
  watch([preference, resolved], () => {
    document.documentElement.dataset.theme = resolved.value
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', resolved.value === 'dark' ? '#08111f' : '#f3f6fa')
    try { localStorage.setItem('argus.theme', preference.value) } catch { /* Session-only fallback. */ }
  }, { immediate: true })
  onBeforeUnmount(() => media.removeEventListener('change', onChange))
  return { preference, resolved, presentation }
}
