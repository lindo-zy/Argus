// 升级流水线演示数据层（规划页 · Mock 实现）：基线安装 → 前置检查 → 包分发 → 执行升级 → 版本校验 → 升级后冒烟。
// 全部数据保存在本浏览器（argus.upgrade.v1），不占用真实设备、不提交 Jenkins、不改主资源快照。
import { createDemoStore, localStamp, defaultDemoStorage } from './demoStore.js'

export const UPGRADE_DATA_KEY = 'argus.upgrade.v1'

// 阶段顺序即执行顺序；duration 为演示用时（秒），success/failed/pending/running 复用平台状态徽章。
export const UPGRADE_STAGES = [
  { key: 'baseline', name: '基线版本安装', detail: '写入基线镜像并完成首次启动', duration: 32 },
  { key: 'precheck', name: '升级前置检查', detail: '磁盘空间、健康状态与升级依赖检查', duration: 6 },
  { key: 'transfer', name: '升级包分发', detail: '校验包摘要并分发到目标环境', duration: 18 },
  { key: 'apply', name: '执行升级', detail: '应用升级包并重启进入新版本', duration: 45 },
  { key: 'verify', name: '版本校验', detail: '确认系统版本与包清单一致', duration: 4 },
  { key: 'smoke', name: '升级后冒烟', detail: '核心服务与基础功能冒烟', duration: 27 },
]

const ARCHES = ['ARM64', 'x86_64']
const SHA256_RE = /^[0-9a-f]{64}$/i

// 固定历史样本截至 2026-10-30，与主资源池的演示窗口保持一致。
function upgradeSeed() {
  const pipelines = [
    { id: 'UP-2001', name: 'OS 24.03 → 24.04 升级验证', description: '覆盖基线安装、整包升级与升级后核心冒烟的标准验证链', arch: 'x86_64', baseVersion: 'OS 24.03', targetVersion: 'OS 24.04', packageRef: 'jfrog://argus/os-upgrade/24.04-r2.tar.zst', packageSha256: '3f8a9c2e5b1d7f40a6c8e2d4b6f80a1c3e5d7f90b2a4c6e8d0f2a4b6c8e0d2f4', status: 'published', createdBy: '王磊', createdAt: '2026-10-27', updatedAt: '2026-10-29' },
    { id: 'UP-2002', name: 'ARM64 OTA 增量升级', description: 'OTA 差分包升级与断点恢复验证', arch: 'ARM64', baseVersion: 'OS 24.03', targetVersion: 'OS 24.03-R2', packageRef: 'jfrog://argus/os-upgrade/24.03-r2-ota-arm64.zip', packageSha256: 'b1d3f5a7c9e0f2b4a6c8d0e2f4a6b8c0d2e4f6a8b0c2d4e6f8a0b2c4d6e8f0a2', status: 'published', createdBy: '张明', createdAt: '2026-10-28', updatedAt: '2026-10-29' },
    { id: 'UP-2003', name: '安全补丁升级', description: '季度安全补丁合集升级验证（待补充包摘要）', arch: 'x86_64', baseVersion: 'OS 24.03', targetVersion: 'OS 24.03-R3-sec', packageRef: 'jfrog://argus/os-upgrade/24.03-r3-sec.tar.zst', packageSha256: '0e2a4c6e8d0f2a4b6c8e0d2f4a6b8c0d2e4f6a8b0c2d4e6f8a0b2c4d6e8f0a2b', status: 'draft', createdBy: '李四', createdAt: '2026-10-30', updatedAt: '2026-10-30' },
  ]
  const stageRow = (status, duration, error = '') => ({ status, ...(duration !== undefined ? { duration } : {}), ...(error ? { error } : {}) })
  const successStages = () => UPGRADE_STAGES.map(s => ({ key: s.key, name: s.name, ...stageRow('success', s.duration) }))
  const runs = [
    { id: 'UPL-3001', pipelineId: 'UP-2001', pipelineName: pipelines[0].name, envId: 'ENV-X86-02', envName: 'x86 稳定性环境', arch: 'x86_64', baseVersion: 'OS 24.03', targetVersion: 'OS 24.04', versionAfter: 'OS 24.04', packageRef: pipelines[0].packageRef, packageSha256: pipelines[0].packageSha256, status: 'success', stages: successStages(), error: '', user: '张明', started: '2026-10-30 09:12', ended: '2026-10-30 09:36' },
    { id: 'UPL-3002', pipelineId: 'UP-2002', pipelineName: pipelines[1].name, envId: 'ENV-ARM-05', envName: 'ARM64 性能基准环境', arch: 'ARM64', baseVersion: 'OS 24.03', targetVersion: 'OS 24.03-R2', versionAfter: '', packageRef: pipelines[1].packageRef, packageSha256: pipelines[1].packageSha256, status: 'failed', stages: UPGRADE_STAGES.map((s, i) => ({ key: s.key, name: s.name, ...stageRow(i < 3 ? 'success' : i === 3 ? 'failed' : 'pending', i === 3 ? 45 : i < 3 ? s.duration : undefined, i === 3 ? '升级脚本退出码 1：/data 分区空间不足，已回退基线版本' : '') })), error: '升级脚本退出码 1：/data 分区空间不足，已回退基线版本', user: '张明', started: '2026-10-30 14:05', ended: '2026-10-30 14:23' },
    { id: 'UPL-3003', pipelineId: 'UP-2001', pipelineName: pipelines[0].name, envId: 'ENV-VM-03', envName: '虚拟化冒烟环境', arch: 'x86_64', baseVersion: 'OS 24.03', targetVersion: 'OS 24.04', versionAfter: 'OS 24.04', packageRef: pipelines[0].packageRef, packageSha256: pipelines[0].packageSha256, status: 'success', stages: successStages(), error: '', user: '李四', started: '2026-10-29 10:40', ended: '2026-10-29 11:02' },
    { id: 'UPL-3004', pipelineId: 'UP-2002', pipelineName: pipelines[1].name, envId: 'ENV-ARM-01', envName: 'ARM64 兼容性环境', arch: 'ARM64', baseVersion: 'OS 24.03', targetVersion: 'OS 24.03-R2', versionAfter: 'OS 24.03-R2', packageRef: pipelines[1].packageRef, packageSha256: pipelines[1].packageSha256, status: 'success', stages: successStages(), error: '', user: '王磊', started: '2026-10-29 16:20', ended: '2026-10-29 16:44' },
  ]
  return {
    schema: 1, revision: 0, pipelines, runs,
    activities: [
      { time: '14:23', text: 'UPL-3002 执行升级失败，已回退基线版本', tone: 'red' },
      { time: '09:36', text: 'UPL-3001 升级成功，版本已到 OS 24.04', tone: 'green' },
      { time: '09:12', text: '张明 启动了 UPL-3001 升级验证', tone: 'blue' },
    ],
  }
}

function text(value, label, max, required = true) {
  if (value === undefined || value === null) value = ''
  if (typeof value !== 'string' || value !== value.trim()) throw new Error(`${label}格式无效。`)
  if (required && !value.trim()) throw new Error(`请填写${label}。`)
  if (value.trim().length > max) throw new Error(`${label}不能超过 ${max} 字。`)
  return value.trim()
}

function validateUpgradePayload(data, payload) {
  if (Object.keys(payload).some(key => !['id', 'name', 'description', 'arch', 'baseVersion', 'targetVersion', 'packageRef', 'packageSha256', 'actor'].includes(key))) throw new Error('包含未知参数，升级配置只能声明受控字段。')
  const name = text(payload.name, '流水线名称', 60)
  const description = text(payload.description, '描述', 200, false)
  if (!ARCHES.includes(payload.arch)) throw new Error('请选择有效的处理器架构。')
  const baseVersion = text(payload.baseVersion, '基线版本', 40)
  const targetVersion = text(payload.targetVersion, '目标版本', 40)
  if (baseVersion === targetVersion) throw new Error('目标版本不能与基线版本相同。')
  const packageRef = text(payload.packageRef, '升级包引用', 200)
  const packageSha256 = text(payload.packageSha256, '升级包 SHA-256 摘要', 64)
  if (!SHA256_RE.test(packageSha256)) throw new Error('升级包摘要必须是 64 位十六进制 SHA-256，用于完整性校验。')
  return { name, description, arch: payload.arch, baseVersion, targetVersion, packageRef, packageSha256 }
}

let shared = null
export function getUpgradeStore() {
  return shared ||= createUpgradeStore({ storage: defaultDemoStorage() })
}

export function createUpgradeStore({ storage, latency, now = () => new Date() } = {}) {
  const kernel = createDemoStore({ key: UPGRADE_DATA_KEY, storage, latency, now, seed: upgradeSeed })
  const runningStage = run => run.stages.find(stage => stage.status === 'running') || run.stages.find(stage => stage.status === 'pending')
  const stamp = () => localStamp(now())

  return {
    load: kernel.load,
    saveUpgradePipeline(payload) {
      return kernel.mutate(data => {
        const fields = validateUpgradePayload(data, payload)
        let pipeline
        if (payload.id) {
          pipeline = data.pipelines.find(item => item.id === payload.id)
          if (!pipeline) throw new Error('升级流水线不存在，请刷新后重试。')
          if (pipeline.status !== 'draft') throw new Error('已发布配置不可修改，请复制为新草稿。')
        } else {
          pipeline = { id: kernel.nextId('UP', data.pipelines), status: 'draft', createdBy: payload.actor?.name || '演示用户', createdAt: now().toISOString().slice(0, 10) }
          data.pipelines.unshift(pipeline)
        }
        Object.assign(pipeline, fields, { updatedAt: now().toISOString().slice(0, 10) })
        kernel.activity(data, `${payload.actor?.name || '演示用户'} ${payload.id ? '更新了' : '新建了'}升级配置草稿 ${pipeline.name}`, 'blue')
        return { id: pipeline.id }
      })
    },
    copyUpgradePipeline(id, actor = {}) {
      return kernel.mutate(data => {
        const source = data.pipelines.find(item => item.id === id)
        if (!source) throw new Error('升级流水线不存在。')
        const copy = { ...source, id: kernel.nextId('UP', data.pipelines), name: (source.name + ' · 副本').slice(0, 60), status: 'draft', createdBy: actor.name || '演示用户', createdAt: now().toISOString().slice(0, 10), updatedAt: now().toISOString().slice(0, 10) }
        data.pipelines.unshift(copy)
        kernel.activity(data, `${actor.name || '演示用户'} 复制了升级配置 ${source.name}`, 'blue')
        return { id: copy.id }
      })
    },
    publishUpgradePipeline(id, actor = {}) {
      return kernel.mutate(data => {
        kernel.requireAdmin(actor, '发布升级流水线')
        const pipeline = data.pipelines.find(item => item.id === id)
        if (!pipeline) throw new Error('升级流水线不存在。')
        if (pipeline.status === 'published') throw new Error('该配置已发布。')
        // 只校验受控字段本身；status/createdBy 等生命周期字段不参与校验。
        validateUpgradePayload(data, { name: pipeline.name, description: pipeline.description, arch: pipeline.arch, baseVersion: pipeline.baseVersion, targetVersion: pipeline.targetVersion, packageRef: pipeline.packageRef, packageSha256: pipeline.packageSha256, actor })
        pipeline.status = 'published'
        pipeline.updatedAt = now().toISOString().slice(0, 10)
        kernel.activity(data, `${actor.name} 发布了升级配置 ${pipeline.name}`, 'green')
        return { id: pipeline.id }
      })
    },
    startUpgradeRun({ pipelineId, envId, envName, envArch, actor = {} } = {}) {
      return kernel.mutate(data => {
        const pipeline = data.pipelines.find(item => item.id === pipelineId)
        if (!pipeline || pipeline.status !== 'published') throw new Error('只有已发布的升级配置可以启动演示运行。')
        if (typeof envId !== 'string' || !envId.trim()) throw new Error('请选择目标环境。')
        if (envArch && envArch !== pipeline.arch) throw new Error('环境架构与升级配置不匹配。')
        if (data.runs.some(run => run.status === 'running' && run.pipelineId === pipelineId)) throw new Error('该配置已有运行中的升级，请等待完成后再启动。')
        const run = {
          id: kernel.nextId('UPL', data.runs), pipelineId, pipelineName: pipeline.name,
          envId: envId.trim(), envName: envName || envId, arch: pipeline.arch,
          baseVersion: pipeline.baseVersion, targetVersion: pipeline.targetVersion, versionAfter: '',
          packageRef: pipeline.packageRef, packageSha256: pipeline.packageSha256, status: 'running',
          stages: UPGRADE_STAGES.map((stage, index) => ({ key: stage.key, name: stage.name, status: index === 0 ? 'running' : 'pending' })),
          error: '', user: actor.name || '演示用户',
          started: stamp(),
          ended: '',
        }
        data.runs.unshift(run)
        kernel.activity(data, `${run.user} 启动了 ${run.pipelineName} 升级演示（${run.envName}）`, 'blue')
        return { id: run.id }
      })
    },
    // 推进一个阶段：当前阶段成功、下一阶段进入 running；最后一段完成后记录升级后版本。
    tickUpgradeRun(id) {
      return kernel.mutate(data => {
        const run = data.runs.find(item => item.id === id)
        if (!run) throw new Error('升级运行不存在。')
        if (run.status !== 'running') throw new Error('该运行已结束，无需推进。')
        const current = runningStage(run)
        const definition = UPGRADE_STAGES.find(stage => stage.key === current.key)
        current.status = 'success'
        current.duration = definition?.duration ?? 10
        const next = run.stages.find(stage => stage.status === 'pending')
        if (next) next.status = 'running'
        else {
          run.status = 'success'
          run.versionAfter = run.targetVersion
          run.ended = stamp()
          kernel.activity(data, `${run.id} 升级成功，版本已到 ${run.versionAfter}`, 'green')
        }
        return { id: run.id, status: run.status }
      })
    },
    failUpgradeRun(id, { reason } = {}) {
      return kernel.mutate(data => {
        const run = data.runs.find(item => item.id === id)
        if (!run) throw new Error('升级运行不存在。')
        if (run.status !== 'running') throw new Error('该运行已结束。')
        const current = runningStage(run)
        current.status = 'failed'
        current.error = reason || '演示运行被手动标记失败'
        run.status = 'failed'
        run.error = current.error
        run.ended = stamp()
        kernel.activity(data, `${run.id} 在「${current.name}」阶段失败：${current.error}`, 'red')
        return { id: run.id }
      })
    },
  }
}
