// 临时诊断脚本：SSR 渲染 App 首屏，捕获渲染期运行时错误（无需浏览器）。
import { Window } from 'happy-dom'
import { createServer } from 'vite'
import { renderToString } from 'vue/server-renderer'
import { createSSRApp } from 'vue'

const window = new Window()
globalThis.window = window
globalThis.document = window.document
globalThis.localStorage = window.localStorage
Object.defineProperty(globalThis, 'navigator', { value: window.navigator, configurable: true })
globalThis.matchMedia = window.matchMedia?.bind(window) ?? (() => ({ matches: false, addEventListener() {}, removeEventListener() {} }))
globalThis.addEventListener = window.addEventListener.bind(window)
globalThis.removeEventListener = window.removeEventListener.bind(window)
globalThis.scrollTo = () => {}
globalThis.location = window.location
window.location.href = 'http://127.0.0.1:5173/#/environments'

const server = await createServer({ server: { middlewareMode: true }, appType: 'custom', logLevel: 'error' })
try {
  const { default: App } = await server.ssrLoadModule('/src/App.vue')
  const app = createSSRApp(App)
  const html = await renderToString(app)
  const checks = [['侧栏品牌', 'ARGUS'], ['导航-环境资源', '环境资源'], ['导航-人员管理', '人员管理'], ['加载态', '正在准备工作空间']]
  console.log('SSR 首屏渲染成功，长度', html.length)
  for (const [label, needle] of checks) console.log(' ', label, html.includes(needle) ? 'OK' : '缺失!')

  // 数据加载后的完整渲染：用种子快照直接渲染两个核心视图（props 与 App.vue 接线一致）。
  const { default: EnvironmentsView } = await server.ssrLoadModule('/src/views/EnvironmentsView.vue')
  const { default: WorkspaceView } = await server.ssrLoadModule('/src/views/WorkspaceView.vue')
  const { createSeed, enrichEnvironments } = await server.ssrLoadModule('/src/mock/seed.js')
  const snapshot = createSeed()
  const envs = enrichEnvironments(snapshot)
  const poolSnapshot = { ...snapshot, devices: snapshot.devices.filter(d => d.lifecycle !== 'retired') }
  const retired = snapshot.environments.filter(e => e.lifecycle === 'retired')
  const envApp = createSSRApp(EnvironmentsView, { envs, snapshot: poolSnapshot, retired, canAdmin: true, cols: { arch: true, type: true, owner: true, version: true, location: true, tags: true, devices: true, status: true } })
  const envHtml = await renderToString(envApp)
  console.log('EnvironmentsView(默认列表) 渲染成功，长度', envHtml.length)
  for (const [label, needle] of [['列表渲染', 'ARM64 兼容性环境'], ['结构分布', '资源结构分布'], ['分页', '每页 6 个'], ['宿主机列', '宿主机 host-']]) {
    console.log(' ', label, envHtml.includes(needle) ? 'OK' : '缺失!')
  }
  // 切到卡片视图再渲染一次，验证环境卡片与宿主机行
  globalThis.localStorage = { getItem: key => (key === 'argus.envView' ? 'grid' : null), setItem() {}, removeItem() {}, clear() {} }
  const gridApp = createSSRApp(EnvironmentsView, { envs, snapshot: poolSnapshot, retired, canAdmin: true, cols: { arch: true, type: true, owner: true, version: true, location: true, tags: true, devices: true, status: true } })
  const gridHtml = await renderToString(gridApp)
  for (const [label, needle] of [['环境卡片', 'IP：'], ['卡片宿主机', '宿主机：']]) {
    console.log(' ', label, gridHtml.includes(needle) ? 'OK' : '缺失!')
  }
  const wsApp = createSSRApp(WorkspaceView, { page: 'people', envs, snapshot: poolSnapshot, currentUser: snapshot.users[0], canAdmin: true })
  const wsHtml = await renderToString(wsApp)
  console.log('WorkspaceView(people) 渲染成功，长度', wsHtml.length, '| 王磊', wsHtml.includes('王磊') ? 'OK' : '缺失!')
} catch (error) {
  console.error('SSR 渲染崩溃:', error?.stack || error)
  process.exitCode = 1
} finally {
  await server.close()
}
