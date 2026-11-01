"""流水线 DAG 草稿：归一化、校验、保存并发与 REST 契约，仅使用 conftest 独立测试库。"""
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.errors import BusinessError
from app.models import PipelineDefinition, PipelineVersion
from app.services.pipelines import CASE_CATALOG, draft_content, normalize_graph, save_pipeline, topological_order, validate_pipeline

ADMIN, MEMBER = 'U-1001', 'U-1002'


def ref(case_id='PERF-001'):
    item = next(c for c in CASE_CATALOG if c['id'] == case_id)
    return {'id': item['id'], 'version': item['version']}


def valid_graph():
    return {'schema': 1, 'nodes': [
        {'id': 'n1', 'type': 'install', 'name': '环境安装', 'position': {'x': 70, 'y': 160},
         'config': {'envId': '', 'imageRef': 'demo://os-25.03/build-108'}},
        {'id': 'n2', 'type': 'cases', 'name': '性能用例', 'position': {'x': 360, 'y': 160},
         'config': {'caseRefs': [ref('PERF-001')], 'mode': 'sequential', 'failure': 'stop'}},
    ], 'edges': [{'id': 'e1', 'source': 'n1', 'target': 'n2'}]}


def save_payload(graph=None, **extra):
    payload = {'name': '回归编排草稿', 'description': '测试用', 'arch': 'ARM64', 'graph': graph or valid_graph()}
    payload.update(extra)
    return payload


def test_normalize_graph_keeps_shape_and_orders():
    graph = normalize_graph(valid_graph(), 'ARM64')
    assert [n['id'] for n in graph['nodes']] == ['n1', 'n2']
    assert graph['nodes'][1]['config']['caseRefs'] == [ref('PERF-001')]
    assert topological_order(graph) == ['n1', 'n2']


def test_normalize_graph_rejects_unknown_type_and_duplicate_id():
    graph = valid_graph()
    graph['nodes'][1]['type'] = 'script'
    with pytest.raises(BusinessError, match='未知节点类型'):
        normalize_graph(graph, 'ARM64')
    graph = valid_graph()
    graph['nodes'][1]['id'] = 'n1'
    with pytest.raises(BusinessError, match='不能重复'):
        normalize_graph(graph, 'ARM64')


def test_normalize_graph_rejects_cycle_self_edge_and_duplicate_edge():
    graph = valid_graph()
    graph['edges'].append({'id': 'e2', 'source': 'n2', 'target': 'n1'})
    with pytest.raises(BusinessError, match='循环依赖'):
        normalize_graph(graph, 'ARM64')
    graph = valid_graph()
    graph['edges'].append({'id': 'e2', 'source': 'n1', 'target': 'n1'})
    with pytest.raises(BusinessError, match='连接自身'):
        normalize_graph(graph, 'ARM64')
    graph = valid_graph()
    graph['edges'].append({'id': 'e2', 'source': 'n1', 'target': 'n2'})
    with pytest.raises(BusinessError, match='不能重复连接'):
        normalize_graph(graph, 'ARM64')


def test_normalize_graph_rejects_case_version_drift_and_unknown_config():
    graph = valid_graph()
    graph['nodes'][1]['config']['caseRefs'] = [{'id': 'PERF-001', 'version': 'v999'}]
    with pytest.raises(BusinessError, match='版本已变化'):
        normalize_graph(graph, 'ARM64')
    graph = valid_graph()
    graph['nodes'][1]['config']['job'] = 'free-job'
    with pytest.raises(BusinessError, match='配置无效'):
        normalize_graph(graph, 'ARM64')


def test_normalize_graph_rejects_oversized_graph():
    graph = {'schema': 1, 'nodes': [
        {'id': f'n{i}', 'type': 'cases', 'name': f'节点{i}', 'position': {'x': 10, 'y': 10},
         'config': {'caseRefs': [], 'mode': 'sequential', 'failure': 'stop'}} for i in range(51)],
        'edges': []}
    with pytest.raises(BusinessError, match='50 个节点'):
        normalize_graph(graph, 'ARM64')


def test_validate_reports_structure_issues(session):
    empty = validate_pipeline(session, MEMBER, {'arch': 'ARM64', 'graph': {'schema': 1, 'nodes': [], 'edges': []}})
    assert empty['valid'] is False
    assert any('用例执行节点' in i['message'] for i in empty['issues'])

    graph = valid_graph()
    graph['nodes'][0]['config']['imageRef'] = ''
    graph['nodes'].append({'id': 'n4', 'type': 'install', 'name': '备用安装', 'position': {'x': 70, 'y': 380},
                           'config': {'envId': '', 'imageRef': 'demo://os-25.03/build-109'}})
    graph['nodes'].append({'id': 'n3', 'type': 'cases', 'name': '组件用例', 'position': {'x': 650, 'y': 160},
                           'config': {'caseRefs': [ref('PERF-002')], 'mode': 'sequential', 'failure': 'continue'}})
    graph['edges'].append({'id': 'e2', 'source': 'n2', 'target': 'n3'})
    graph['edges'].append({'id': 'e3', 'source': 'n4', 'target': 'n3'})
    result = validate_pipeline(session, MEMBER, {'arch': 'ARM64', 'graph': graph})
    messages = ' '.join(i['message'] for i in result['issues'])
    assert '镜像引用' in messages and '仅继承一个上游安装环境' in messages


def test_validate_passes_consistent_graph_and_returns_order(session):
    result = validate_pipeline(session, MEMBER, {'arch': 'ARM64', 'graph': valid_graph()})
    assert result['valid'] is True
    assert result['issues'] == []
    assert result['order'] == ['n1', 'n2']
    assert result['source'] == 'demo'


def test_save_creates_draft_then_updates_with_revision(session):
    created = save_pipeline(session, MEMBER, save_payload())
    assert created['id'].startswith('PL-') and created['editRevision'] == 1
    definition = session.get(PipelineDefinition, created['id'])
    assert (definition.name, definition.architecture) == ('回归编排草稿', 'ARM64')
    updated = save_pipeline(session, MEMBER, save_payload(name='回归编排草稿二', id=created['id'], expectedRevision=1))
    assert updated['editRevision'] == 2
    version = session.execute(select(PipelineVersion).where(PipelineVersion.definition_id == created['id'])).scalar_one()
    assert version.schema_version == 2 and version.status == 'draft'
    assert draft_content(version)['graph']['nodes'][0]['config']['imageRef'] == 'demo://os-25.03/build-108'


def test_save_rejects_stale_revision_with_conflict_code(session):
    created = save_pipeline(session, MEMBER, save_payload())
    with pytest.raises(BusinessError) as exc:
        save_pipeline(session, MEMBER, save_payload(id=created['id'], expectedRevision=0))
    assert exc.value.code == 'pipeline_conflict'


def test_save_rejects_published_definition_and_unknown_payload_keys(session):
    with pytest.raises(BusinessError, match='已发布配置不可修改'):
        save_pipeline(session, MEMBER, save_payload(id='os-smoke-test', expectedRevision=0))
    with pytest.raises(BusinessError, match='未知参数'):
        save_pipeline(session, MEMBER, save_payload(job='free-job'))


def test_save_rejects_cycle_before_touching_existing_draft(session):
    created = save_pipeline(session, MEMBER, save_payload())
    graph = valid_graph()
    graph['edges'].append({'id': 'e2', 'source': 'n2', 'target': 'n1'})
    before = session.execute(select(PipelineVersion)).scalars().all()
    with pytest.raises(BusinessError, match='循环依赖'):
        save_pipeline(session, MEMBER, save_payload(id=created['id'], expectedRevision=1, graph=graph))
    assert session.execute(select(PipelineVersion)).scalars().all() == before


def test_draft_content_blocks_external_entities():
    poisoned = SimpleNamespace(schema_version=2, xml='<!DOCTYPE pipeline [<!ENTITY x SYSTEM "file:///etc/passwd">]><pipeline />')
    with pytest.raises(BusinessError, match='外部实体'):
        draft_content(poisoned)


def test_rest_validate_and_draft_roundtrip(client):
    body = client.post('/api/pipelines/validate', json={'arch': 'ARM64', 'graph': valid_graph()},
                       headers={'X-Argus-User': MEMBER})
    assert body.status_code == 200 and body.json()['valid'] is True
    saved = client.post('/api/pipelines/drafts', json=save_payload(), headers={'X-Argus-User': MEMBER})
    assert saved.status_code == 200
    result = saved.json()['result']
    pipelines = {p['id']: p for p in saved.json()['snapshot']['pipelines']}
    assert pipelines[result['id']]['graph']['nodes'][0]['type'] == 'install'
    assert pipelines[result['id']]['editRevision'] == 1
    stale = client.post('/api/pipelines/drafts',
                        json=save_payload(id=result['id'], expectedRevision=result['editRevision'] - 1),
                        headers={'X-Argus-User': MEMBER})
    assert stale.status_code == 400 and stale.json()['code'] == 'pipeline_conflict'
    cycle = valid_graph()
    cycle['edges'].append({'id': 'e2', 'source': 'n2', 'target': 'n1'})
    rejected = client.post('/api/pipelines/validate', json={'arch': 'ARM64', 'graph': cycle},
                           headers={'X-Argus-User': MEMBER})
    assert rejected.status_code == 400


def test_snapshot_exposes_case_catalog(client):
    snapshot = client.get('/api/snapshot').json()
    assert {c['id'] for c in snapshot['testCases']} >= {'PERF-001', 'PERF-002'}
