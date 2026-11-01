import test from 'node:test'
import assert from 'node:assert/strict'
import { createMockApi } from '../src/mock/api.js'
import { createSeed, DATA_KEY } from '../src/mock/seed.js'

function storage() {
  const values = new Map()
  return { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) }
}
const setup = () => createMockApi({ storage: storage(), latency: 0, now: () => new Date('2026-10-30T07:00:00Z') })
const user = (snapshot, id) => snapshot.users.find(u => u.id === id)

test('members are blocked from delete-class operations while admins keep full access', async () => {
  const api = setup()
  await api.load()
  await api.switchUser('U-1003')
  await assert.rejects(api.archiveEnvironment('ENV-ARM-01'), /移出环境仅管理员可执行/)
  await assert.rejects(api.retireDevice('DEV-001'), /移出设备仅管理员可执行/)
  const before = (await api.load()).snapshot.revision
  await assert.rejects(api.reset(), /重置演示数据仅管理员可执行/)
  assert.equal((await api.load()).snapshot.revision, before)
  await api.switchUser('U-1001')
  const archived = await api.archiveEnvironment('ENV-ARM-01')
  assert.equal(archived.result.lifecycle, 'retired')
})

test('people management is admin-only and protects the current identity', async () => {
  const api = setup()
  await api.load()
  await api.switchUser('U-1003')
  await assert.rejects(api.addUser({ name: '赵倩' }), /新增人员仅管理员可执行/)
  await assert.rejects(api.setUserRole('U-1002', 'admin'), /调整角色仅管理员可执行/)
  await assert.rejects(api.setUserStatus('U-1002', 'disabled'), /停用或启用人员仅管理员可执行/)
  await api.switchUser('U-1001')
  await assert.rejects(api.setUserRole('U-1001', 'member'), /自己/)
  await assert.rejects(api.setUserStatus('U-1001', 'disabled'), /自己/)
  await assert.rejects(api.setUserRole('U-1002', 'owner'), /角色/)
  const added = await api.addUser({ name: '赵倩', role: 'member' })
  assert.equal(added.result.status, 'active')
  await assert.rejects(api.addUser({ name: '赵倩' }), /同名/)
})

test('role promotion takes effect immediately and always leaves one active admin', async () => {
  const api = setup()
  await api.load()
  await api.setUserRole('U-1002', 'admin')
  await api.switchUser('U-1002')
  const archived = await api.archiveEnvironment('ENV-ARM-01')
  assert.equal(archived.result.lifecycle, 'retired')
  await api.setUserRole('U-1001', 'member')
  await api.setUserStatus('U-1001', 'disabled')
  await assert.rejects(api.switchUser('U-1001'), /停用/)
  await assert.rejects(api.setUserRole('U-1002', 'member'), /自己/)
  await assert.rejects(api.setUserStatus('U-1002', 'disabled'), /自己/)
  const { snapshot } = await api.load()
  assert.ok(snapshot.users.some(u => u.role === 'admin' && u.status === 'active'))
})

test('switching identity changes lease ownership checks and borrow attribution', async () => {
  const api = setup()
  const { snapshot } = await api.load()
  const adminLease = snapshot.leases.find(l => l.user === '王磊')
  await api.switchUser('U-1003')
  await assert.rejects(api.renew(adminLease.id), /只能操作自己的借用记录/)
  const borrowed = await api.borrow({ envId: 'ENV-VM-03', hours: 4, purpose: '成员借用验证' })
  assert.equal(borrowed.result.lease.user, '李四')
  await assert.rejects(api.switchUser('U-1003'), /已是此身份/)
  await api.switchUser('U-1001')
  await api.setUserStatus('U-1003', 'disabled')
  await assert.rejects(api.switchUser('U-1003'), /停用/)
})

test('schema-1 snapshots migrate to the personnel model without losing data', async () => {
  const local = storage()
  const seed = createSeed()
  const legacy = { ...seed, users: undefined, currentUserId: undefined }
  delete legacy.users
  delete legacy.currentUserId
  legacy.schema = 1
  legacy.activities.unshift({ time: '09:00', text: '迁移前遗留的活动记录', tone: 'blue' })
  local.setItem(DATA_KEY, JSON.stringify(legacy))
  const { snapshot } = await createMockApi({ storage: local, latency: 0 }).load()
  assert.equal(snapshot.schema, 2)
  assert.equal(snapshot.users.length, 4)
  assert.equal(user(snapshot, 'U-1001').role, 'admin')
  assert.equal(snapshot.currentUserId, 'U-1001')
  assert.ok(snapshot.activities.some(a => a.text.includes('迁移前遗留')))
})
