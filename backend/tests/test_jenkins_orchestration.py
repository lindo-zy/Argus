"""Real HTTP Jenkins protocol tests against an isolated DB and local mock server."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
import json
from threading import Event, Thread
from urllib.request import urlopen

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.errors import BusinessError, PermissionError
from app.jenkins_mock import make_server
from app.models import BackgroundTask, Lease, LeaseResource, PipelineExecution, PipelineRun, PipelineVersion
from app.services import now
from app.services.jenkins import JenkinsClient, JenkinsError
from app.services.orchestration import start_execution, tick
from app.services.pipelines import copy_pipeline, draft_content, publish_pipeline, save_pipeline
from app.services.runs import cleanup_run, finish_run
from app.state import device_occupancy
from test_pipelines import ADMIN, MEMBER, save_payload, valid_graph


@pytest.fixture
def jenkins(tmp_path, monkeypatch):
    servers = []
    def start(**kwargs):
        server = make_server(port=0, state=str(tmp_path / f'jenkins-{len(servers)}.sqlite'), queue_delay=0, build_delay=0, **kwargs)
        thread = Thread(target=server.serve_forever, daemon=True); thread.start()
        servers.append((server, thread))
        url = f'http://127.0.0.1:{server.server_port}'
        monkeypatch.setattr(settings(), 'jenkins_url', url)
        monkeypatch.setattr(settings(), 'jenkins_kind', 'mock')
        return url
    yield start
    for server, thread in servers:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def published(session, graph=None):
    result = save_pipeline(session, MEMBER, save_payload(graph))
    released = publish_pipeline(session, ADMIN, result['id'], {'expectedRevision': result['editRevision'], 'contentHash': result['contentHash']})
    session.commit()
    return released


def begin(session, pid, key='run-1', env='ENV-ARM-01'):
    result = start_execution(session, MEMBER, {'pipelineVersionId': pid['pipelineVersionId'], 'envId': env, 'executionMode': 'jenkins', 'requestKey': key})
    session.commit()
    return result


def advance(engine, run_id, count=1, factory=JenkinsClient):
    sf = sessionmaker(bind=engine, expire_on_commit=False)
    for _ in range(count):
        with sf() as s:
            task = s.execute(select(BackgroundTask).where(BackgroundTask.run_id == run_id)).scalar_one()
            if task.status == 'done': return
            task.next_attempt_at = now() - timedelta(seconds=1); s.commit()
        assert tick(sf, factory)


def submissions(url):
    with urlopen(url + '/healthz') as response: return json.load(response)['submissions']


def test_copy_both_draft_and_published_are_independent(session):
    source = published(session)
    before = session.get(PipelineVersion, source['pipelineVersionId']).xml
    for _ in range(2):
        copied = copy_pipeline(session, MEMBER, source['id'])
        target = session.execute(select(PipelineVersion).where(PipelineVersion.definition_id == copied['id'])).scalar_one()
        assert target.status == 'draft' and draft_content(target)['graph'] == valid_graph()
        assert copied['id'] != source['id']
    assert session.get(PipelineVersion, source['pipelineVersionId']).xml == before
    legacy = copy_pipeline(session, MEMBER, 'os-smoke-test')
    version = session.execute(select(PipelineVersion).where(PipelineVersion.definition_id == legacy['id'])).scalar_one()
    assert draft_content(version)['graph']['nodes'][0]['config']['imageRef'] == ''


def test_publish_separate_permission_revision_and_validation(session):
    saved = save_pipeline(session, MEMBER, save_payload())
    payload = {'expectedRevision': 1, 'contentHash': saved['contentHash']}
    with pytest.raises(PermissionError): publish_pipeline(session, MEMBER, saved['id'], payload)
    with pytest.raises(BusinessError, match='配置已变化'):
        publish_pipeline(session, ADMIN, saved['id'], {**payload, 'expectedRevision': 0})
    publish_pipeline(session, ADMIN, saved['id'], payload)
    with pytest.raises(BusinessError, match='不可修改'):
        save_pipeline(session, MEMBER, save_payload(id=saved['id'], expectedRevision=1))
    copied = copy_pipeline(session, MEMBER, 'os-smoke-test')
    with pytest.raises(BusinessError, match='镜像引用'):
        publish_pipeline(session, ADMIN, copied['id'], {'expectedRevision': 1, 'contentHash': copied['contentHash']})


def test_http_dag_success_evidence_and_cleanup(session, engine, jenkins):
    url = jenkins(); run = begin(session, published(session))
    execution = session.get(PipelineExecution, run['id'])
    assert execution.snapshot['contentHash'] and execution.snapshot['devices']
    assert run['evidence'] == 0 and run['executionMode'] == 'mock'
    assert all('token' not in n and 'params' not in n for n in run['nodes'])
    advance(engine, run['id'], 10); session.expire_all()
    row = session.get(PipelineRun, run['id']); execution = session.get(PipelineExecution, run['id'])
    assert (row.status, row.result, row.progress, row.total, row.passed) == ('cleaning', 'success', 100, 0, 0)
    assert [n['status'] for n in execution.nodes] == ['success', 'success']
    assert submissions(url) == 2 and row.evidence_count == 2
    for refs in row.evidence_refs.values():
        with urlopen(refs['console']) as response: assert b'No device or test execution' in response.read()
    assert all(state == 'cleaning' for did, state in device_occupancy(session, 'WS-DEFAULT').items() if did in execution.snapshot['environments'][0]['deviceIds'])
    with pytest.raises(BusinessError, match='不能模拟结束'): finish_run(session, MEMBER, run['id'])
    cleanup_run(session, MEMBER, run['id'], True); session.commit()
    assert session.get(Lease, row.lease_id).status == 'released'
    assert session.scalar(select(func.count()).select_from(LeaseResource).where(LeaseResource.lease_id == row.lease_id, LeaseResource.active.is_(True))) == 0


@pytest.mark.parametrize('policy, expected', [('stop', ['success', 'failed', 'blocked']), ('continue', ['success', 'failed', 'success'])])
def test_failure_policy_preserves_final_failure(session, engine, jenkins, policy, expected):
    url = jenkins(fail_nodes=['n2']); graph = valid_graph()
    graph['nodes'][1]['config']['failure'] = policy
    third = deepcopy(graph['nodes'][1]); third.update(id='n3', name='后续用例')
    graph['nodes'].append(third); graph['edges'].append({'id': 'e2', 'source': 'n2', 'target': 'n3'})
    run = begin(session, published(session, graph)); advance(engine, run['id'], 20); session.expire_all()
    assert [n['status'] for n in session.get(PipelineExecution, run['id']).nodes] == expected
    assert session.get(PipelineRun, run['id']).result == 'failed'
    assert submissions(url) == (2 if policy == 'stop' else 3)


def test_lost_submission_response_reconciles_without_resubmit(session, engine, jenkins):
    url = jenkins(drop_nodes=['n1']); run = begin(session, published(session))
    advance(engine, run['id']); session.expire_all()
    assert session.get(PipelineRun, run['id']).status == 'reconciling' and submissions(url) == 1
    with pytest.raises(BusinessError, match='终态'): cleanup_run(session, MEMBER, run['id'], True)
    advance(engine, run['id'], 12); session.expire_all()
    assert session.get(PipelineRun, run['id']).result == 'success' and submissions(url) == 2


def test_worker_crash_after_acceptance_recovers_intent(session, engine, jenkins):
    url = jenkins(); run = begin(session, published(session))
    class CrashClient(JenkinsClient):
        def submit(self, job, params):
            super().submit(job, params)
            raise RuntimeError('simulated process crash after acceptance')
    with pytest.raises(RuntimeError): advance(engine, run['id'], factory=CrashClient)
    session.expire_all(); task = session.execute(select(BackgroundTask).where(BackgroundTask.run_id == run['id'])).scalar_one()
    task.claim_until = now() - timedelta(seconds=1); session.commit()
    assert session.get(PipelineExecution, run['id']).nodes[0]['status'] == 'submitting'
    advance(engine, run['id'], 12); session.expire_all()
    assert session.get(PipelineRun, run['id']).result == 'success' and submissions(url) == 2


def test_request_replay_and_conflict(session, jenkins):
    jenkins(); pipeline = published(session)
    first = begin(session, pipeline); second = begin(session, pipeline)
    assert first['id'] == second['id']
    with pytest.raises(BusinessError, match='不同参数'): begin(session, pipeline, env='ENV-009')
    with pytest.raises(BusinessError, match='执行任务'): begin(session, pipeline, key='run-2')
    with pytest.raises(BusinessError, match='不能模拟结束'): finish_run(session, MEMBER, first['id'])


def test_concurrent_start_same_key_creates_one_lease(session, engine, jenkins):
    jenkins(); pipeline = published(session); sf = sessionmaker(bind=engine, expire_on_commit=False)
    def start(_):
        with sf() as s: return begin(s, pipeline)['id']
    with ThreadPoolExecutor(max_workers=2) as pool: ids = list(pool.map(start, range(2)))
    assert len(set(ids)) == 1


def test_two_workers_only_one_submits(session, engine, jenkins):
    url = jenkins(); run = begin(session, published(session)); sf = sessionmaker(bind=engine, expire_on_commit=False)
    entered, release = Event(), Event()
    class BlockingClient(JenkinsClient):
        def submit(self, job, params):
            with sf() as observer: assert observer.get(PipelineExecution, run['id']).nodes[0]['status'] == 'submitting'
            entered.set(); assert release.wait(5)
            return super().submit(job, params)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(tick, sf, BlockingClient); assert entered.wait(5)
        assert tick(sf) is False
        release.set(); assert future.result(timeout=5)
    assert submissions(url) == 1


def test_unknown_never_resubmits_or_releases(session, engine, jenkins):
    url = jenkins(); run = begin(session, published(session))
    class UnknownClient(JenkinsClient):
        def submit(self, job, params): raise JenkinsError('acceptance unknown')
    advance(engine, run['id'], factory=UnknownClient); advance(engine, run['id'], 4); session.expire_all()
    assert submissions(url) == 0 and session.get(PipelineRun, run['id']).status == 'reconciling'
    assert session.scalar(select(func.count()).select_from(LeaseResource).where(LeaseResource.active.is_(True), LeaseResource.lease_id == session.get(PipelineRun, run['id']).lease_id)) > 0


def test_rest_copy_publish_run_and_replay(client, jenkins):
    jenkins(); headers = {'X-Argus-User': MEMBER}
    copied = client.post('/api/pipelines/arm-compatibility/copy', headers=headers).json(); pid = copied['result']['id']
    p = next(p for p in copied['snapshot']['pipelines'] if p['id'] == pid)
    p['graph']['nodes'][0]['config']['imageRef'] = 'mock://os/fixed-build-1'
    assert p['graph']['nodes'][1]['config']['caseRefs'] == []
    p['graph']['nodes'][1]['config']['caseRefs'] = valid_graph()['nodes'][1]['config']['caseRefs']
    saved = client.post('/api/pipelines/drafts', headers=headers, json={'id': pid, 'name': p['name'], 'arch': p['arch'], 'graph': p['graph'], 'expectedRevision': p['editRevision']}).json()['result']
    payload = {'expectedRevision': saved['editRevision'], 'contentHash': saved['contentHash']}
    assert client.post(f'/api/pipelines/{pid}/publish', headers=headers, json=payload).status_code == 403
    response = client.post(f'/api/pipelines/{pid}/publish', headers={'X-Argus-User': ADMIN}, json=payload)
    assert response.status_code == 200
    run_payload = {'pipelineVersionId': response.json()['result']['pipelineVersionId'], 'envId': 'ENV-ARM-01', 'executionMode': 'jenkins', 'requestKey': 'rest-run-1'}
    a = client.post('/api/runs', headers=headers, json=run_payload); b = client.post('/api/runs', headers=headers, json=run_payload)
    assert a.status_code == b.status_code == 200 and a.json()['result']['id'] == b.json()['result']['id']
    assert a.json()['snapshot']['scheduler']['kind'] == 'mock'
    assert client.post(f"/api/runs/{a.json()['result']['id']}/finish", headers=headers, json={'success': True}).status_code == 400


def test_install_failure_blocks_cases_and_isolation_keeps_history(session, engine, jenkins):
    url = jenkins(fail_nodes=['n1']); run = begin(session, published(session))
    advance(engine, run['id'], 12); session.expire_all()
    execution = session.get(PipelineExecution, run['id'])
    assert [n['status'] for n in execution.nodes] == ['failed', 'blocked'] and submissions(url) == 1
    cleanup_run(session, MEMBER, run['id'], False); session.commit()
    assert session.get(PipelineRun, run['id']).cleanup_state == 'isolated'
    assert session.get(Lease, session.get(PipelineRun, run['id']).lease_id).status == 'isolated'
    assert execution.snapshot['environments'][0]['deviceIds']


def test_configuration_drift_retains_intent_without_submitting(session, engine, jenkins, monkeypatch):
    url = jenkins(); run = begin(session, published(session))
    monkeypatch.setattr(settings(), 'jenkins_cases_job', 'different-job')
    advance(engine, run['id']); session.expire_all()
    assert session.get(PipelineRun, run['id']).status == 'reconciling' and submissions(url) == 0


def test_multi_environment_snapshot_cleanup_and_shared_install_rejected(session, engine, jenkins):
    from app.models import Environment
    from app.services.leases import _borrow_failure
    from app.state import env_device_ids
    jenkins(); occupancy = device_occupancy(session, 'WS-DEFAULT')
    first = session.get(Environment, 'ENV-ARM-01')
    first_devices = set(env_device_ids(session, 'WS-DEFAULT', first.id))
    second = next(e for e in session.execute(select(Environment).where(Environment.architecture == 'ARM64')).scalars()
                  if e.id != first.id and not _borrow_failure(session, e, occupancy)
                  and not first_devices.intersection(env_device_ids(session, 'WS-DEFAULT', e.id)))
    graph = valid_graph(); graph['nodes'][0]['config']['envId'] = first.id
    install2, cases2 = deepcopy(graph['nodes']); install2['id'], cases2['id'] = 'i2', 'c2'
    install2['config']['envId'] = second.id
    graph['nodes'].extend([install2, cases2]); graph['edges'].append({'id': 'branch2', 'source': 'i2', 'target': 'c2'})
    run = begin(session, published(session, graph)); advance(engine, run['id'], 20); session.expire_all()
    execution = session.get(PipelineExecution, run['id'])
    assert len(execution.snapshot['environments']) == 2
    assert all(device_occupancy(session, 'WS-DEFAULT')[d['id']] == 'cleaning' for d in execution.snapshot['devices'])
    cleanup_run(session, MEMBER, run['id'], True); session.commit()
    graph['nodes'][2]['config']['envId'] = first.id
    with pytest.raises(BusinessError, match='共享设备'):
        begin(session, published(session, graph), key='second-run')


def test_expired_worker_cannot_overwrite_reclaimed_owner(session, engine, jenkins):
    url = jenkins(); run = begin(session, published(session)); sf = sessionmaker(bind=engine, expire_on_commit=False)
    entered, release = Event(), Event()
    class SlowClient(JenkinsClient):
        def submit(self, job, params):
            result = super().submit(job, params); entered.set(); assert release.wait(5)
            return result
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(tick, sf, SlowClient); assert entered.wait(5)
        with sf() as s:
            task = s.execute(select(BackgroundTask).where(BackgroundTask.run_id == run['id'])).scalar_one()
            task.claim_until = now() - timedelta(seconds=1); s.commit()
        assert tick(sf)
        release.set(); assert future.result(timeout=5)
    session.expire_all()
    assert session.get(PipelineExecution, run['id']).nodes[0]['build'] == '1' and submissions(url) == 1
