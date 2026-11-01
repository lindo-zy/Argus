import pipelineCases from './pipeline-cases.json' with { type: 'json' }

export const CURRENT_USER = '王磊'
export const DATA_KEY = 'argus.mock.v1'
export const statusMeta = {
  available: { label: '可借用', tone: 'green' },
  leased: { label: '已借用', tone: 'blue' },
  running: { label: '执行中', tone: 'blue' },
  maintenance: { label: '维护中', tone: 'amber' },
  isolated: { label: '已隔离', tone: 'red' },
  reconciling: { label: '待对账', tone: 'amber' },
  cleaning: { label: '待清理', tone: 'amber' },
  checking: { label: '待健康复核', tone: 'amber' },
  success: { label: '已完成', tone: 'green' },
  failed: { label: '未通过', tone: 'red' },
  queued: { label: '排队中', tone: 'muted' },
  active: { label: '借用中', tone: 'blue' },
  released: { label: '已归还', tone: 'muted' },
  published: { label: '已发布', tone: 'green' },
  draft: { label: '草稿', tone: 'muted' },
  pending: { label: '待回归', tone: 'amber' },
  disabled: { label: '已停用', tone: 'muted' },
}

export const DEFAULT_USER_ID = 'U-1001'

export function createSeed() {
  const users = [
    { id: 'U-1001', name: '王磊', role: 'admin', status: 'active', title: '系统测试组 · 负责人', joined: '2026-01-12' },
    { id: 'U-1002', name: '张明', role: 'member', status: 'active', title: '系统测试组 · 测试工程师', joined: '2026-02-20' },
    { id: 'U-1003', name: '李四', role: 'member', status: 'active', title: '系统测试组 · 测试工程师', joined: '2026-03-05' },
    { id: 'U-1004', name: '陈静', role: 'member', status: 'active', title: '质量组 · 测试工程师', joined: '2026-06-18' },
  ]
  const devices = []
  const environments = []
  const groups = [
    ['ENV-ARM-01', 'ARM64 兼容性环境', 'ARM64', 'physical', 4, 'available', '兼容性验证', 'core'],
    ['ENV-X86-02', 'x86 稳定性环境', 'x86_64', 'physical', 6, 'running', '稳定性回归', 'core'],
    ['ENV-VM-03', '虚拟化冒烟环境', 'x86_64', 'virtual', 3, 'available', '核心冒烟', 'smoke'],
    ['ENV-ST-04', '存储故障注入环境', 'ARM64', 'physical', 4, 'maintenance', '故障注入', 'storage'],
    ['ENV-ARM-05', 'ARM64 性能基准环境', 'ARM64', 'physical', 3, 'leased', '性能基准', 'performance'],
    ['ENV-NET-06', '网络协议验证环境', 'ARM64', 'physical', 2, 'available', '共享存储节点', 'network'],
  ]
  const names = ['安装升级', '内核回归', '安全验证', '驱动兼容', '容器集成', '可靠性', '文件系统', '桌面兼容', '启动冒烟', '虚拟化回归', '网络压力', '系统服务', '长稳验证', '镜像验证', '故障恢复', '调度性能', '内存管理', '基础冒烟']
  const states = ['available', 'leased', 'isolated', 'running', 'available', 'available', 'cleaning', 'available', 'running', 'available', 'maintenance', 'available', 'reconciling', 'available', 'leased', 'available', 'available', 'leased']
  names.forEach((name, i) => groups.push([
    `ENV-${String(i + 7).padStart(3, '0')}`, `${name}环境`, i % 3 ? 'x86_64' : 'ARM64',
    i % 2 ? 'virtual' : 'physical', 2, states[i], name, i % 2 ? 'smoke' : 'core',
  ]))
  groups.forEach(([id, name, arch, type, count, status, description, suite], i) => {
    const deviceIds = []
    for (let j = 0; j < count; j++) {
      const deviceId = `DEV-${String(devices.length + 1).padStart(3, '0')}`
      deviceIds.push(deviceId)
      devices.push({
        id: deviceId, name: `${arch === 'ARM64' ? 'arm' : 'x86'}-${type === 'virtual' ? 'vm' : 'node'}-${String(devices.length + 1).padStart(2, '0')}`,
        arch, type, status, ip: `10.24.${i + 1}.${j + 11}`, host: type === 'virtual' ? `host-${String(j % 4 + 1).padStart(2, '0')}` : '',
        cpu: arch === 'ARM64' ? 'Kunpeng 920 · 32 核' : 'Xeon Gold · 24 核',
        memory: type === 'virtual' ? '32 GB' : '128 GB', location: type === 'virtual' ? '虚拟资源池 / 集群 A' : `实验室 A / 机柜 ${Math.floor(i / 4) + 1}`,
      })
    }
    environments.push({ id, name, arch, type, deviceIds, description, suite, version: 'OS 24.03', owner: status === 'leased' ? CURRENT_USER : '张明', tags: [arch, type === 'physical' ? '物理机' : '虚拟机'], health: '2026-10-30 14:28', location: '南京 · 系统测试实验室' })
  })
  // Environment membership deliberately overlaps: one device cannot be borrowed twice.
  environments[5].deviceIds.push(environments[3].deviceIds[0])
  const leases = environments.filter(e => devices.find(d => d.id === e.deviceIds[0]).status === 'leased').map((e, i) => ({
    id: `LEASE-${1001 + i}`, envId: e.id, user: CURRENT_USER, purpose: i === 0 ? '性能基线复测与数据采集' : '版本功能验证',
    status: 'active', start: '2026-10-30T09:00:00+08:00', end: '2026-10-31T09:00:00+08:00', hours: 24,
  }))
  const runs = [
    { id: 'RUN-1028', envId: 'ENV-X86-02', name: '稳定性回归', status: 'running', progress: 68, passed: 76, total: 112, duration: 32, build: '#218', result: null },
    { id: 'RUN-1027', envId: 'ENV-VM-03', name: '核心冒烟', status: 'success', progress: 100, passed: 112, total: 112, duration: 18, build: '#217', result: 'success' },
    { id: 'RUN-1026', envId: environments[12].id, name: '内核兼容性验证', status: 'cleaning', progress: 100, passed: 43, total: 48, duration: 26, build: '#216', result: 'failed' },
    { id: 'RUN-1025', envId: environments[18].id, name: '长稳测试', status: 'reconciling', progress: 82, passed: 39, total: 48, duration: 48, build: '#215', result: null },
    { id: 'RUN-1024', envId: 'ENV-ARM-01', name: 'ARM64 启动验证', status: 'success', progress: 100, passed: 48, total: 48, duration: 12, build: '#214', result: 'success' },
    { id: 'RUN-1023', envId: environments[9].id, name: '驱动兼容回归', status: 'running', progress: 35, passed: 20, total: 64, duration: 14, build: '#213', result: null },
    { id: 'RUN-1022', envId: environments[14].id, name: '启动冒烟', status: 'running', progress: 76, passed: 35, total: 48, duration: 21, build: '#212', result: null },
    { id: 'RUN-1021', envId: 'ENV-VM-03', name: '虚拟机网络验证', status: 'failed', progress: 100, passed: 61, total: 64, duration: 16, build: '#211', result: 'failed' },
  ].map((r, i) => ({ ...r, version: 'OS 24.03 RC2', user: i % 2 ? '李四' : '张明', started: `2026-10-30 ${String(14 - Math.floor(i / 2)).padStart(2, '0')}:0${i}`, artifact: `os-24.03-rc2-${environments.find(e => e.id === r.envId)?.arch.toLowerCase()}.img`, config: 'os-smoke-test@v3', evidence: ['success', 'failed'].includes(r.status) ? 5 : r.status === 'cleaning' ? 4 : 3 }))
  const maintenance = [
    { id: 'MT-003', envId: 'ENV-ST-04', reason: '共享存储节点磁盘健康异常，替换介质后复核', status: 'maintenance', user: '李四', date: '2026-10-30 11:18', issue: 'RDC-2086' },
    { id: 'MT-002', envId: environments[16].id, reason: '网络接口固件升级', status: 'maintenance', user: '张明', date: '2026-10-30 09:30', issue: 'RDC-2080' },
    { id: 'MT-001', envId: environments[8].id, reason: '设备失联，等待人工健康复核', status: 'isolated', user: '系统', date: '2026-10-29 16:20', issue: 'RDC-2072' },
  ]
  // Argus 声明式配置只读示例（契约见 docs/03-接口与数据契约.md）；输出与后端 services/pipelines.py 保持一致。
  const pipelineXml = ({ id, name, version, job, arch }) => `<pipeline id="${id}" name="${name}" version="${version.replace(/^v/, '')}">
  <jenkins connection="jenkins-main" job="${job}" />
  <environment>
    <requirements>
      <item name="architecture" value="${arch}" />
    </requirements>
    <occupancy mode="exclusive" />
  </environment>
  <parameters>
    <parameter name="IMAGE_REF" type="string" required="true" />
    <parameter name="TEST_SUITE" type="enum" required="true" default="smoke">
      <option value="smoke" />
      <option value="network" />
    </parameter>
  </parameters>
  <execution timeoutMinutes="120" />
</pipeline>`
  const pipelines = [
    { id: 'os-smoke-test', name: '操作系统核心冒烟', version: 'v3', status: 'published', job: 'os-smoke-test', arch: 'x86_64', date: '2026-10-29', description: '安装、启动、网络及核心系统服务验证' },
    { id: 'arm-compatibility', name: 'ARM64 兼容性回归', version: 'v2', status: 'published', job: 'arm-compatibility', arch: 'ARM64', date: '2026-10-28', description: 'ARM64 平台硬件与系统兼容性验证' },
    { id: 'storage-chaos', name: '存储故障注入', version: 'v1', status: 'draft', job: 'storage-chaos', arch: 'ARM64', date: '2026-10-30', description: '受控故障场景与恢复能力验证' },
  ].map(pipeline => ({ ...pipeline, xml: pipelineXml(pipeline) }))
  const issues = [
    { id: 'RDC-2086', title: '存储节点 I/O 超时', severity: 'P1', module: '存储', owner: '李四', status: 'pending' },
    { id: 'RDC-2080', title: '网卡固件升级后链路抖动', severity: 'P2', module: '网络', owner: '张明', status: 'pending' },
    { id: 'RDC-2072', title: '安全验证节点偶发失联', severity: 'P2', module: '基础设施', owner: '王磊', status: 'pending' },
    { id: 'RDC-2065', title: '虚拟机 DNS 解析超时', severity: 'P2', module: '网络', owner: '李四', status: 'success' },
  ]
  return { schema: 2, revision: 0, users, currentUserId: DEFAULT_USER_ID, devices, environments, leases, runs, maintenance, pipelines, issues, testCases: pipelineCases,
    activities: [
      { time: '14:26', text: '张明 启动了 RUN-1028 稳定性回归任务', tone: 'blue' },
      { time: '13:50', text: '王磊 借用了 ARM64 性能基准环境', tone: 'green' },
      { time: '11:18', text: 'ENV-ST-04 进入维护状态', tone: 'amber' },
      { time: '09:42', text: 'RUN-1027 执行完成，证据已归档', tone: 'green' },
    ],
  }
}

const priority = ['isolated', 'maintenance', 'reconciling', 'cleaning', 'checking', 'running', 'leased']
// Read legacy single-environment records without resetting existing local data.
export function environmentIdsFor(record) {
  return [...new Set(Array.isArray(record?.envIds) ? record.envIds : record?.envId ? [record.envId] : [])]
}

export function deviceIdsFor(record, snapshot) {
  if (Array.isArray(record?.deviceIds)) return [...new Set(record.deviceIds)]
  return [...new Set(environmentIdsFor(record).flatMap(id => snapshot.environments.find(e => e.id === id)?.deviceIds || []))]
}

export function environmentStatus(env, devices) {
  const states = env.deviceIds.map(id => devices.find(d => d.id === id)?.status || 'isolated')
  return priority.find(status => states.includes(status)) || 'available'
}

export function enrichEnvironments(snapshot) {
  return snapshot.environments.filter(env => env.lifecycle !== 'retired').map(env => {
    const devices = snapshot.devices.filter(d => env.deviceIds.includes(d.id) && d.lifecycle !== 'retired')
    return {
      ...env, status: environmentStatus(env, snapshot.devices), devices,
      ip: [...new Set(devices.map(d => d.ip).filter(Boolean))].join(', '),
      host: [...new Set(devices.map(d => d.host).filter(Boolean))].join(', '),
      shared: env.deviceIds.some(id => snapshot.environments.filter(e => e.lifecycle !== 'retired' && e.deviceIds.includes(id)).length > 1),
      run: snapshot.runs.find(r => r.envId === env.id && ['running', 'cleaning', 'reconciling'].includes(r.status)),
      lease: snapshot.leases.find(l => environmentIdsFor(l).includes(env.id) && ['active', 'checking'].includes(l.status)),
    }
  })
}

// Historical fixture, independent from the current resource snapshot. Units and scope remain explicit.
export function overview(version = 'OS 24.03 RC2', days = 7) {
  const rc1 = version.endsWith('RC1')
  const tests = rc1 ? [112, 144, 96, 192, 144, 160, 176] : [144, 160, 112, 256, 160, 192, 224]
  const fails = rc1 ? [8, 6, 8, 12, 9, 6, 7] : [7, 4, 6, 10, 4, 4, 5]
  const counts = rc1 ? [10, 12, 11, 18, 13, 18, 16] : [14, 18, 13, 24, 16, 21, 22]
  const points = tests.map((total, i) => ({ label: `10/${24 + i}`, total, failed: fails[i], runs: counts[i], rate: (total - fails[i]) / total * 100 })).slice(-days)
  const total = points.reduce((s, p) => s + p.total, 0)
  const failed = points.reduce((s, p) => s + p.failed, 0)
  const runs = points.reduce((s, p) => s + p.runs, 0)
  const envFailure = Math.round(failed * .45), caseFailure = Math.round(failed * .3)
  return { points, total, passed: total - failed, rate: ((total - failed) / total * 100).toFixed(1), runs,
    utilization: rc1 ? '67.2' : '72.4', prepare: rc1 ? '11.2' : '8.6', evidence: rc1 ? '94.9' : '98.4', pending: rc1 ? 18 : 12,
    failures: [{ label: '环境异常', count: envFailure, tone: 'red' }, { label: '用例失败', count: caseFailure, tone: 'blue' }, { label: '基础设施', count: failed - envFailure - caseFailure, tone: 'teal' }],
  }
}
