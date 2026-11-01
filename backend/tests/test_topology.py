"""拓扑原子提交与 PostgreSQL 竞争验证，仅使用 conftest 独立测试库。"""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.errors import BusinessError
from app.models import Environment, ResourceEvent
from app.services import WORKSPACE_ID
from app.services import environments, leases
from app.services.topology import save_topology
from app.state import env_device_ids
ADMIN, MEMBER = 'U-1001', 'U-1002'


def target_env(session):
    target = environments.save_environment(session, ADMIN, {
        'name': '拓扑目标环境', 'arch': 'ARM64', 'type': 'physical', 'deviceIds': ['DEV-003']})['id']
    session.commit()
    return target


def migration(session, target):
    source_ids = env_device_ids(session, WORKSPACE_ID, 'ENV-ARM-01')
    target_ids = env_device_ids(session, WORKSPACE_ID, target)
    return {'changes': [
        {'envId': 'ENV-ARM-01', 'expectedDeviceIds': source_ids, 'deviceIds': [d for d in source_ids if d != 'DEV-002']},
        {'envId': target, 'expectedDeviceIds': target_ids, 'deviceIds': target_ids + ['DEV-002']}]}


def test_rest_migration_and_replay(session, client):
    target = target_env(session)
    payload = migration(session, target)
    response = client.post('/api/topology', json=payload, headers={'X-Argus-User': MEMBER})
    assert response.status_code == 200
    body = response.json()
    assert body['result']['changed'] == 2
    envs = {e['id']: e for e in body['snapshot']['environments']}
    assert 'DEV-002' not in envs['ENV-ARM-01']['deviceIds']
    assert 'DEV-002' in envs[target]['deviceIds']
    assert envs[target]['health'] == '待检查'
    repeat = client.post('/api/topology', json=payload, headers={'X-Argus-User': MEMBER})
    assert repeat.status_code == 400
    assert repeat.json()['code'] == 'topology_conflict'
    session.rollback()
    assert 'DEV-002' in env_device_ids(session, WORKSPACE_ID, target)


def test_batch_target_failure_is_atomic(session):
    target = target_env(session)
    payload = migration(session, target)
    before = session.execute(select(ResourceEvent.id)).scalars().all()
    payload['changes'][1]['deviceIds'].append('DEV-005')
    with pytest.raises(BusinessError, match='架构、类型'):
        save_topology(session, MEMBER, payload)
    assert 'DEV-002' in env_device_ids(session, WORKSPACE_ID, 'ENV-ARM-01')
    assert 'DEV-002' not in env_device_ids(session, WORKSPACE_ID, target)
    assert session.execute(select(ResourceEvent.id)).scalars().all() == before


@pytest.mark.parametrize('kind', ['last', 'stale', 'duplicate', 'retired', 'busy', 'isolated'])
def test_denials(session, kind):
    target = target_env(session)
    payload = migration(session, target)
    if kind == 'last': payload['changes'][0]['deviceIds'] = []
    elif kind == 'stale': payload['changes'][1]['expectedDeviceIds'] = ['DEV-001']
    elif kind == 'duplicate': payload['changes'].append(payload['changes'][0])
    elif kind == 'retired':
        session.get(Environment, target).lifecycle = 'retired'; session.commit()
    elif kind == 'busy':
        leases.borrow(session, MEMBER, {'envIds': ['ENV-ARM-01'], 'hours': 4, 'purpose': '占用保护'}, 'topo-busy'); session.commit()
    else:
        from app.models import Device
        session.get(Device, 'DEV-002').operational_state = 'isolated'; session.commit()
    with pytest.raises(BusinessError): save_topology(session, MEMBER, payload)
    assert 'DEV-002' in env_device_ids(session, WORKSPACE_ID, 'ENV-ARM-01')


def test_add_preserves_source(session):
    target = target_env(session)
    payload = migration(session, target); payload['changes'] = payload['changes'][1:]
    save_topology(session, MEMBER, payload); session.commit()
    assert 'DEV-002' in env_device_ids(session, WORKSPACE_ID, 'ENV-ARM-01')
    assert 'DEV-002' in env_device_ids(session, WORKSPACE_ID, target)


def test_identity_denial(session, client):
    target = target_env(session); payload = migration(session, target)
    from app.models import User
    session.get(User, MEMBER).status = 'disabled'; session.commit()
    for actor, code in [(MEMBER, 400), ('missing', 401)]:
        response = client.post('/api/topology', json=payload, headers={'X-Argus-User': actor})
        assert response.status_code == code
    assert 'DEV-002' in env_device_ids(session, WORKSPACE_ID, 'ENV-ARM-01')


def test_borrow_waits_for_topology_and_captures_new_snapshot(session, lock_engine):
    target = target_env(session); payload = migration(session, target)
    save_topology(session, ADMIN, payload)
    started = Event()
    def borrower():
        with Session(lock_engine) as other:
            started.set()
            result = leases.borrow(other, MEMBER, {'envIds': ['ENV-ARM-01'], 'hours': 4, 'purpose': '并发借用'}, 'after-topology')
            other.commit(); return result
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(borrower); assert started.wait(2)
        session.commit(); result = future.result(timeout=5)
    assert 'DEV-002' not in result['lease']['deviceIds']


def test_topology_waits_for_borrow_and_rejects(session, lock_engine):
    target = target_env(session); payload = migration(session, target)
    leases.borrow(session, MEMBER, {'envIds': ['ENV-ARM-01'], 'hours': 4, 'purpose': '并发保护'}, 'before-topology')
    started = Event()
    def editor():
        with Session(lock_engine) as other:
            started.set()
            try:
                save_topology(other, ADMIN, payload); other.commit(); return 'unexpected success'
            except BusinessError as error:
                other.rollback(); return error.message
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(editor); assert started.wait(2)
        session.commit(); assert '释放后' in future.result(timeout=5)
    assert 'DEV-002' in env_device_ids(session, WORKSPACE_ID, 'ENV-ARM-01')


def test_mcp_uses_same_atomic_service(session, monkeypatch):
    from app import mcp_server
    from app.services import get_user
    target = target_env(session); payload = migration(session, target)
    monkeypatch.setattr(mcp_server, '_session', lambda: session)
    monkeypatch.setattr(mcp_server, '_actor', lambda sess, ctx: get_user(sess, MEMBER))
    result = mcp_server.save_environment_topology(payload['changes'])
    assert result['changed'] == 2
    assert 'DEV-002' not in env_device_ids(session, WORKSPACE_ID, 'ENV-ARM-01')


def test_concurrent_edit_rejects_stale_associations(session, lock_engine):
    target = target_env(session); payload = migration(session, target)
    save_topology(session, ADMIN, payload)
    started = Event()
    def editor():
        with Session(lock_engine) as other:
            started.set()
            try:
                save_topology(other, MEMBER, payload); other.commit(); return 'unexpected success'
            except BusinessError as error:
                other.rollback(); return error.code
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(editor); assert started.wait(2)
        session.commit(); assert future.result(timeout=5) == 'topology_conflict'
