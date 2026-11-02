"""Durable DAG → Jenkins coordinator. No database lock spans network I/O.

The worker advances one node per claimed run at a time. Ready branches are
serialized, including shared-device branches; dependencies and failure policy
remain meaningful. A persisted 'submitting' intent is reconciled after a crash,
never automatically submitted again. Unknown results retain the resource lease.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
import hashlib
import json
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..errors import BusinessError
from ..models import (BackgroundTask, Device, Environment, Lease, LeaseEnvironment,
                      LeaseResource, PipelineDefinition, PipelineExecution, PipelineRun, PipelineVersion)
from ..state import device_occupancy, env_device_ids
from . import WORKSPACE_ID, get_user, now, record_event, text_field
from .jenkins import JenkinsClient, JenkinsError, connection_config
from .leases import _borrow_failure
from .pipelines import draft_content, topological_order, validate_pipeline
from .resource_locks import lock_resources

TERMINAL = {'success', 'failed', 'blocked'}


def start_execution(session: Session, actor_id: str, payload: dict) -> dict:
    from .runs import run_dict_of
    actor = get_user(session, actor_id)
    if set(payload) - {'pipelineId', 'pipelineVersionId', 'envId', 'requestKey', 'executionMode'}:
        raise BusinessError('运行请求包含未知参数。')
    if payload.get('executionMode') != 'jenkins':
        raise BusinessError('运行模式必须明确指定为 jenkins。')
    for field in ('pipelineId', 'pipelineVersionId', 'envId'):
        if field in payload and (not isinstance(payload[field], str) or len(payload[field]) > 64):
            raise BusinessError('流水线与环境标识格式无效。')
    key = text_field(payload.get('requestKey'), '运行请求键', 128, True)
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    # Serialize a request key across processes before occupancy checks or ID creation.
    session.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))'),
                    {'key': f'argus-run:{actor.id}:{key}'})
    prior = session.execute(select(PipelineExecution).where(
        PipelineExecution.actor_id == actor.id, PipelineExecution.request_key == key)).scalar_one_or_none()
    if prior:
        if prior.request_hash != fingerprint:
            raise BusinessError('运行请求键已用于不同参数。', code='request_conflict')
        return run_dict_of(session, session.get(PipelineRun, prior.run_id))
    config = connection_config()
    if payload.get('pipelineVersionId'):
        version = session.get(PipelineVersion, payload['pipelineVersionId'])
    else:
        version = session.execute(select(PipelineVersion).where(
            PipelineVersion.definition_id == payload.get('pipelineId'),
            PipelineVersion.status == 'published').order_by(PipelineVersion.published_at.desc())).scalars().first()
    if not version or version.workspace_id != WORKSPACE_ID or version.status != 'published':
        raise BusinessError('只有已发布配置可以启动任务。')
    definition = session.get(PipelineDefinition, version.definition_id)
    graph = draft_content(version)['graph']
    if not graph:
        raise BusinessError('旧 XML 配置请先复制为 DAG，补齐镜像与用例后发布运行。')
    validation = validate_pipeline(session, actor.id, {'arch': definition.architecture, 'graph': graph})
    if not validation['valid']:
        raise BusinessError('编排不可运行：' + '；'.join(i['message'] for i in validation['issues']))
    root_envs = {n['id']: n['config']['envId'] or payload.get('envId') or '' for n in graph['nodes'] if n['type'] == 'install'}
    env_ids = sorted(set(root_envs.values()))
    if not all(env_ids):
        raise BusinessError('请选择安装节点使用的环境，或提供默认运行环境。')
    lock_resources(session, env_ids)
    occupancy = device_occupancy(session, WORKSPACE_ID)
    envs = []
    for eid in env_ids:
        env = session.get(Environment, eid)
        if not env or env.workspace_id != WORKSPACE_ID or env.architecture != definition.architecture:
            raise BusinessError('环境不存在或与流水线架构不匹配。')
        failure = _borrow_failure(session, env, occupancy)
        if failure:
            raise BusinessError(f"{eid}：{failure['reason']}。")
        envs.append(env)
    # Two installations must not overwrite the same physical device before its cases.
    # Multiple case branches may freely inherit the SAME installation node.
    root_devices = set()
    for eid in root_envs.values():
        ids = set(env_device_ids(session, WORKSPACE_ID, eid))
        if root_devices & ids:
            raise BusinessError('多个安装节点的环境存在共享设备，请共用一个安装节点后分支执行用例。')
        root_devices.update(ids)
    run_id, lease_id = 'RUN-' + uuid4().hex[:20], 'LEASE-' + uuid4().hex[:20]
    started = now()
    lease = Lease(id=lease_id, workspace_id=WORKSPACE_ID, kind='run', holder_subject=actor.id,
        purpose=definition.name, status='active', start_at=started, expires_at=started + timedelta(hours=8), hours=8)
    session.add(lease); session.flush()
    snapshots, device_ids = [], set()
    for env in envs:
        ids = sorted(env_device_ids(session, WORKSPACE_ID, env.id))
        snapshots.append({'id': env.id, 'name': env.name, 'arch': env.architecture,
            'topologyVersion': env.topology_version, 'deviceIds': ids})
        session.add(LeaseEnvironment(workspace_id=WORKSPACE_ID, lease_id=lease_id, environment_id=env.id,
            name_snapshot=env.name, topology_version=env.topology_version, device_ids_snapshot=ids))
        device_ids.update(ids)
    devices = []
    for did in sorted(device_ids):
        d = session.get(Device, did)
        snap = {'id': d.id, 'name': d.name, 'arch': d.architecture, 'type': d.resource_type, 'holder': actor.name}
        devices.append(snap)
        session.add(LeaseResource(workspace_id=WORKSPACE_ID, lease_id=lease_id, device_id=did, active=True, device_snapshot=snap))
    planned, roots = [], {}
    by_id = {n['id']: n for n in graph['nodes']}
    for nid in validation['order']:
        n = by_id[nid]
        parents = [e['source'] for e in graph['edges'] if e['target'] == nid]
        root = nid if n['type'] == 'install' else roots[parents[0]]
        roots[nid] = root
        token = uuid4().hex
        image_ref = by_id[root]['config']['imageRef']
        params = {'ARGUS_TOKEN': token, 'RUN_ID': run_id, 'NODE_ID': nid,
                  'ENV_ID': root_envs[root], 'IMAGE_REF': image_ref,
                  'DEVICE_IDS': json.dumps(next(e['deviceIds'] for e in snapshots if e['id'] == root_envs[root])),
                  'CASE_REFS': json.dumps(n['config'].get('caseRefs', []), separators=(',', ':')),
                  'PIPELINE_HASH': version.content_hash}
        planned.append({'id': nid, 'name': n['name'], 'type': n['type'], 'status': 'pending', 'attempt': 1,
            'parents': parents, 'failure': n['config'].get('failure', 'stop'), 'envId': root_envs[root],
            'job': config['jobs'][n['type']], 'token': token, 'params': params,
            'queue': '', 'build': '', 'error': '', 'result': ''})
    snapshot = {'graph': deepcopy(graph), 'pipelineVersionId': version.id, 'contentHash': version.content_hash,
                'connection': config, 'environments': snapshots, 'devices': devices,
                'createdAt': started.isoformat(), 'timeoutSeconds': 7200}
    run = PipelineRun(id=run_id, workspace_id=WORKSPACE_ID, pipeline_version_id=version.id,
        lease_id=lease_id, environment_id=env_ids[0], name=definition.name, params_snapshot={'nodes': [n['params'] for n in planned]},
        image_ref=planned[0]['params']['IMAGE_REF'], jenkins_job=planned[0]['job'], jenkins_queue='', jenkins_build='',
        status='running', started_by=actor.id, cleanup_state='pending', progress=0, total=0, passed=0, evidence_count=0)
    session.add(run); session.flush()
    session.add(PipelineExecution(run_id=run_id, actor_id=actor.id, request_key=key,
        request_hash=fingerprint, snapshot=snapshot, nodes=planned))
    session.add(BackgroundTask(id='JT-' + uuid4().hex[:20], workspace_id=WORKSPACE_ID, run_id=run_id,
        kind='jenkins_dag', status='pending', dedupe_key=run_id, next_attempt_at=started))
    record_event(session, actor_id=actor.id, action='run_activity', entity_type='run', entity_id=run.id,
        payload={'text': f'{run.id} 已创建 Jenkins 调度意图（{config["kind"]}）', 'contentHash': version.content_hash})
    session.flush()
    return run_dict_of(session, run)


def tick(session_factory, client_factory=JenkinsClient) -> bool:
    """Claim → commit intent → one external operation → commit observed state.

    Expired claims are reclaimed with a new owner token, fencing late writers.
    A crashed worker's persisted 'submitting' state can only be reconciled.
    """
    owner = uuid4().hex
    with session_factory() as session:
        task = session.execute(select(BackgroundTask).where(
            BackgroundTask.kind == 'jenkins_dag', BackgroundTask.status.in_(['pending', 'working']),
            (BackgroundTask.next_attempt_at.is_(None) | (BackgroundTask.next_attempt_at <= now())),
            (BackgroundTask.claim_until.is_(None) | (BackgroundTask.claim_until < now()))
        ).order_by(BackgroundTask.next_attempt_at).with_for_update(skip_locked=True).limit(1)).scalar_one_or_none()
        if not task:
            return False
        task.owner, task.status, task.claim_until = owner, 'working', now() + timedelta(seconds=45)
        task.attempts += 1
        task_id, run_id = task.id, task.run_id
        execution = session.get(PipelineExecution, run_id)
        run = session.get(PipelineRun, run_id)
        if not execution or not run or run.status not in ('running', 'reconciling'):
            task.status, task.claim_until = 'done', None
            session.commit(); return True
        nodes = deepcopy(execution.nodes)
        for node in nodes:
            if node['status'] != 'pending':
                continue
            parents = [n for n in nodes if n['id'] in node['parents']]
            if any(p['status'] == 'blocked' or (p['status'] == 'failed' and p['failure'] != 'continue') for p in parents):
                node['status'] = 'blocked'
        active = next((n for n in nodes if n['status'] in ('submitting', 'queued', 'running', 'reconciling')), None)
        submit = False
        if active is None:
            active = next((n for n in nodes if n['status'] == 'pending' and all(
                p['status'] in TERMINAL for p in nodes if p['id'] in n['parents'])), None)
            if active:
                # A run timeout never releases devices or invents a remote terminal result.
                if (now() - run.started_at).total_seconds() > execution.snapshot['timeoutSeconds']:
                    for n in nodes:
                        if n['status'] == 'pending':
                            n['status'], n['error'] = 'blocked', '调度超时，未提交'
                    active = None
                else:
                    active['status'], active['submittedAt'] = 'submitting', now().isoformat()
                    submit = True
        execution.nodes = nodes
        snapshot = deepcopy(execution.snapshot)
        session.commit()
    observation, error = {}, ''
    if active:
        try:
            current = connection_config()
            if current != snapshot['connection']:
                raise JenkinsError('服务端 Jenkins 配置已变化，请恢复原连接后对账')
            client = client_factory(snapshot['connection'])
            observation = ({**client.submit(active['job'], active['params']), 'status': 'queued'} if submit
                           else client.poll(active['job'], active))
        except (JenkinsError, BusinessError) as exc:
            # Adapter errors are fixed safe messages, never raw transport/body exceptions.
            error = (exc.message if isinstance(exc, BusinessError) else str(exc))[:300]
    with session_factory() as session:
        task = session.execute(select(BackgroundTask).where(BackgroundTask.id == task_id).with_for_update()).scalar_one()
        if task.owner != owner:
            return True
        run, execution = session.get(PipelineRun, run_id), session.get(PipelineExecution, run_id)
        nodes = deepcopy(execution.nodes)
        if active:
            node = next(n for n in nodes if n['id'] == active['id'])
            if error:
                node['status'], node['error'] = 'reconciling', error
            else:
                node.update(observation); node['error'] = ''
                if node['status'] in ('queued', 'running') and (now() - run.started_at).total_seconds() > snapshot['timeoutSeconds']:
                    node['status'], node['error'] = 'reconciling', '任务超过调度时限，继续回查终态并保留设备占用。'
            run.jenkins_job = node['job']
            run.jenkins_queue, run.jenkins_build = node.get('queue', ''), node.get('build', '')
        execution.nodes = nodes
        run.duration_minutes = int((now() - run.started_at).total_seconds() / 60)
        run.progress = int(100 * sum(n['status'] in TERMINAL for n in nodes) / len(nodes))
        refs = {n['id']: {'source': snapshot['connection']['kind'], 'build': n['buildUrl'],
                          'console': n['buildUrl'] + 'consoleText'} for n in nodes if n.get('buildUrl')}
        run.evidence_refs, run.evidence_count = refs, len(refs)
        if all(n['status'] in TERMINAL for n in nodes):
            run.status, run.result = 'cleaning', 'success' if all(n['status'] == 'success' for n in nodes) else 'failed'
            run.ended_at, task.status = now(), 'done'
            record_event(session, actor_id=run.started_by, action='run_activity', entity_type='run', entity_id=run.id,
                payload={'text': f'{run.id} Jenkins 节点已收敛（{run.result}），等待设备清理复核'})
        else:
            run.status = 'reconciling' if any(n['status'] == 'reconciling' for n in nodes) else 'running'
            task.status = 'pending'
        task.last_error = error
        task.owner, task.claim_until, task.next_attempt_at = '', None, now() + timedelta(seconds=2)
        session.commit()
    return True
