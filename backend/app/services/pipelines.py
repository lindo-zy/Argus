"""Pipeline draft DAG editing and legacy declarative XML previews.

DAG drafts reuse the version's existing XML content column (schema_version=2).
Only internally generated XML is stored; importing XML or arbitrary jobs is unsupported.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from uuid import uuid4
from xml.etree import ElementTree as ET

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import ARCHES, BusinessError
from ..models import Environment, PipelineDefinition, PipelineVersion
from . import WORKSPACE_ID, get_user, now, record_event, text_field

# Explicit local demo catalog. No external case center or runnable scripts implied.
CASE_CATALOG = json.loads((Path(__file__).parents[1] / 'data/pipeline_cases.json').read_text())


def draft_content(version: PipelineVersion) -> dict:
    if version.schema_version != 2:
        return {'graph': None, 'editRevision': 0, 'updatedAt': ''}
    raw = version.xml or ''
    if '<!DOCTYPE' in raw.upper() or '<!ENTITY' in raw.upper():
        raise BusinessError('编排内容禁止外部实体或文档类型声明。')
    try:
        element = ET.fromstring(raw).find('orchestration')
        data = json.loads(element.text) if element is not None else None
        if not isinstance(data, dict) or type(data.get('editRevision')) is not int or not isinstance(data.get('graph'), dict):
            raise ValueError('invalid DAG content')
        return data
    except (ET.ParseError, ValueError, TypeError) as exc:
        raise BusinessError('流水线编排内容损坏，请保留记录并检查版本。') from exc


def draft_xml(pid: str, name: str, version: str, content: dict) -> str:
    root = ET.Element('pipeline', {'id': pid, 'name': name, 'version': version, 'schemaVersion': '2'})
    ET.SubElement(root, 'orchestration', {'format': 'dag-json'}).text = json.dumps(content, ensure_ascii=False, separators=(',', ':'))
    return ET.tostring(root, encoding='unicode')


def _text(value, label, limit, required=False):
    if not isinstance(value, str):
        raise BusinessError(f'{label}格式无效。')
    return text_field(value, label, limit, required)


def normalize_graph(value: object, arch: str) -> dict:
    if not isinstance(value, dict) or value.get('schema') != 1:
        raise BusinessError('编排格式无效。')
    nodes, edges = value.get('nodes'), value.get('edges')
    if not isinstance(nodes, list) or not isinstance(edges, list) or len(nodes) > 50 or len(edges) > 100:
        raise BusinessError('编排格式无效，最多 50 个节点、100 条连线。')
    catalog = {c['id']: c for c in CASE_CATALOG}
    normalized, ids = [], set()
    for node in nodes:
        if not isinstance(node, dict) or node.get('type') not in ('install', 'cases'):
            raise BusinessError('编排包含未知节点类型。')
        nid = _text(node.get('id'), '节点 ID', 80, True)
        if nid in ids:
            raise BusinessError('节点 ID 不能重复。')
        ids.add(nid)
        position = node.get('position')
        if not isinstance(position, dict) or any(
            type(position.get(key)) not in (int, float) or not math.isfinite(position[key]) or not 0 <= position[key] <= limit
            for key, limit in [('x', 5000), ('y', 3000)]
        ):
            raise BusinessError('节点坐标无效。')
        config = node.get('config')
        if not isinstance(config, dict):
            raise BusinessError('节点配置无效。')
        if node['type'] == 'install':
            if set(config) - {'envId', 'imageRef'}:
                raise BusinessError('环境安装配置包含未知参数。')
            config = {'envId': _text(config.get('envId'), '环境', 64),
                      'imageRef': _text(config.get('imageRef'), '镜像引用', 200)}
        else:
            refs = config.get('caseRefs')
            if (set(config) - {'caseRefs', 'mode', 'failure'} or config.get('mode') != 'sequential'
                    or config.get('failure') not in ('stop', 'continue') or not isinstance(refs, list) or len(refs) > 200):
                raise BusinessError('用例执行配置无效。')
            selected, seen = [], set()
            for ref in refs:
                item = catalog.get(ref.get('id')) if isinstance(ref, dict) and isinstance(ref.get('id'), str) else None
                if not item or item['version'] != ref.get('version'):
                    raise BusinessError('用例不存在或版本已变化，请重新选择。')
                if arch not in item['arches']:
                    raise BusinessError('用例与流水线架构不匹配。')
                if item['id'] in seen:
                    raise BusinessError('同一节点不能重复选择用例。')
                seen.add(item['id'])
                selected.append({'id': item['id'], 'version': item['version']})
            config = {'caseRefs': selected, 'mode': 'sequential', 'failure': config['failure']}
        normalized.append({'id': nid, 'type': node['type'], 'name': _text(node.get('name'), '节点名称', 80, True),
                           'position': {'x': position['x'], 'y': position['y']}, 'config': config})
    normalized_edges, edge_ids, pairs = [], set(), set()
    for edge in edges:
        if (not isinstance(edge, dict) or not isinstance(edge.get('source'), str)
                or not isinstance(edge.get('target'), str) or edge['source'] not in ids or edge['target'] not in ids):
            raise BusinessError('连线引用了不存在的节点。')
        eid = _text(edge.get('id'), '连线 ID', 180, True)
        pair = (edge['source'], edge['target'])
        if pair[0] == pair[1]:
            raise BusinessError('节点不能连接自身。')
        if pair in pairs or eid in edge_ids:
            raise BusinessError('不能重复连接节点。')
        pairs.add(pair); edge_ids.add(eid)
        normalized_edges.append({'id': eid, 'source': pair[0], 'target': pair[1]})
    graph = {'schema': 1, 'nodes': normalized, 'edges': normalized_edges}
    topological_order(graph)
    return graph


def topological_order(graph: dict) -> list[str]:
    counts = {n['id']: 0 for n in graph['nodes']}
    next_ids = {nid: [] for nid in counts}
    for edge in graph['edges']:
        counts[edge['target']] += 1
        next_ids[edge['source']].append(edge['target'])
    queue = [nid for nid in counts if not counts[nid]]
    ordered = []
    while queue:
        nid = queue.pop(0); ordered.append(nid)
        for target in next_ids[nid]:
            counts[target] -= 1
            if not counts[target]:
                queue.append(target)
    if len(ordered) != len(counts):
        raise BusinessError('连线会形成环，DAG 不允许循环依赖。')
    return ordered


def validate_pipeline(session: Session, actor_id: str, payload: dict) -> dict:
    get_user(session, actor_id)
    arch = payload.get('arch')
    if arch not in ARCHES:
        raise BusinessError('请选择有效的处理器架构。')
    graph = normalize_graph(payload.get('graph'), arch)
    issues, roots = [], {}
    if not graph['nodes']:
        issues.append({'message': '请添加环境安装和用例执行节点。'})
    elif not any(n['type'] == 'cases' for n in graph['nodes']):
        issues.append({'message': '请至少添加一个用例执行节点。'})
    nodes = {n['id']: n for n in graph['nodes']}
    for nid in topological_order(graph):
        node = nodes[nid]
        parents = [e['source'] for e in graph['edges'] if e['target'] == nid]
        def issue(message):
            issues.append({'nodeId': nid, 'message': f"{node['name']}：{message}"})
        if node['type'] == 'install':
            roots[nid] = {nid}
            if parents:
                issue('环境安装应作为分支起点。')
            if not node['config']['imageRef']:
                issue('请填写镜像引用。')
            env_id = node['config']['envId']
            env = session.get(Environment, env_id) if env_id else None
            if env_id and (not env or env.workspace_id != WORKSPACE_ID or env.lifecycle != 'active' or env.architecture != arch):
                issue('所选环境已移出或架构不匹配。')
        else:
            roots[nid] = set().union(*(roots[p] for p in parents))
            if len(roots[nid]) != 1:
                issue('请连接且仅继承一个上游安装环境。')
            if not node['config']['caseRefs']:
                issue('请至少选择一条用例。')
    return {'valid': not issues, 'issues': issues, 'order': topological_order(graph), 'source': 'demo'}


def save_pipeline(session: Session, actor_id: str, payload: dict) -> dict:
    actor = get_user(session, actor_id)
    if set(payload) - {'id', 'name', 'description', 'arch', 'graph', 'expectedRevision'}:
        raise BusinessError('编排包含未知参数，不允许指定 Job 或脚本。')
    name = _text(payload.get('name'), '流水线名称', 120, True)
    description = _text(payload.get('description', ''), '描述', 200)
    arch = payload.get('arch')
    if arch not in ARCHES:
        raise BusinessError('请选择有效的处理器架构。')
    graph = normalize_graph(payload.get('graph'), arch)
    pid = payload.get('id')
    if pid:
        pid = _text(pid, '流水线 ID', 64, True)
        definition = session.execute(select(PipelineDefinition).where(
            PipelineDefinition.id == pid, PipelineDefinition.workspace_id == WORKSPACE_ID).with_for_update()).scalar_one_or_none()
        if not definition:
            raise BusinessError('流水线不存在，请刷新后重试。')
        versions = session.execute(select(PipelineVersion).where(PipelineVersion.definition_id == pid).with_for_update()).scalars().all()
        if any(v.status == 'published' for v in versions):
            raise BusinessError('已发布配置不可修改，请创建编排副本。')
        version = max(versions, key=lambda v: (int(v.version.removeprefix('v')) if v.version.removeprefix('v').isdigit() else 0, v.id), default=None)
        expected = payload.get('expectedRevision')
        if not version or type(expected) is not int or expected != draft_content(version)['editRevision']:
            raise BusinessError('草稿已被其他操作更新，请保留当前内容并重新打开最新草稿。', code='pipeline_conflict')
    else:
        pid = f'PL-{uuid4().hex[:20]}'
        definition = PipelineDefinition(id=pid, workspace_id=WORKSPACE_ID, name=name)
        session.add(definition); session.flush()
        version = PipelineVersion(id=f'PV-{uuid4().hex[:20]}', workspace_id=WORKSPACE_ID,
                                  definition_id=pid, version='v1', status='draft', schema_version=1)
        session.add(version)
    definition.name, definition.description, definition.architecture = name, description, arch
    revision = draft_content(version)['editRevision'] + 1
    version.schema_version = 2
    version.xml = draft_xml(pid, name, version.version, {'graph': graph, 'editRevision': revision, 'updatedAt': now().isoformat()})
    version.content_hash = hashlib.sha256(json.dumps({'name': name, 'description': description, 'arch': arch, 'graph': graph},
                                                   sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    record_event(session, actor_id=actor.id, action='pipeline_draft_save', entity_type='pipeline', entity_id=pid,
                 payload={'text': f'{actor.name} 保存了流水线草稿 {name}', 'editRevision': revision})
    session.flush()
    return {'id': pid, 'editRevision': revision, 'contentHash': version.content_hash}


def publish_pipeline(session: Session, actor_id: str, pid: str, payload: dict) -> dict:
    """发布 = 冻结配置（治理动作，仅管理员）+ 动态编排同步 Jenkins Job（尽力而为）。"""
    from . import require_admin
    actor = require_admin(session, actor_id, '发布流水线')
    definition = session.execute(select(PipelineDefinition).where(
        PipelineDefinition.id == pid, PipelineDefinition.workspace_id == WORKSPACE_ID).with_for_update()).scalar_one_or_none()
    if not definition:
        raise BusinessError('流水线不存在。')
    version = session.execute(select(PipelineVersion).where(
        PipelineVersion.definition_id == pid).with_for_update()).scalar_one()
    if version.status == 'published':
        # 幂等重放：内容一致则直接返回；并把上一次未完成的同步补上。
        if payload.get('contentHash') != version.content_hash:
            raise BusinessError('配置已变化，请刷新后重试。', code='pipeline_conflict')
        return _sync_after_publish(session, actor, definition, version)
    content = draft_content(version)
    if (set(payload) != {'expectedRevision', 'contentHash'} or not version.content_hash
            or type(payload['expectedRevision']) is not int or payload['expectedRevision'] != content['editRevision']
            or payload['contentHash'] != version.content_hash):
        raise BusinessError('配置已变化，请重新打开草稿并核对后发布。', code='pipeline_conflict')
    result = validate_pipeline(session, actor.id, {'arch': definition.architecture, 'graph': content['graph']})
    if not result['valid']:
        raise BusinessError('发布前请修复编排：' + '；'.join(i['message'] for i in result['issues']))
    version.status, version.published_by, version.published_at = 'published', actor.id, now()
    record_event(session, actor_id=actor.id, action='pipeline_publish', entity_type='pipeline', entity_id=pid,
                 payload={'text': f'{actor.name} 发布了流水线 {definition.name}', 'contentHash': version.content_hash})
    session.flush()
    return _sync_after_publish(session, actor, definition, version)


def _sync_after_publish(session: Session, actor, definition: PipelineDefinition, version: PipelineVersion) -> dict:
    """发布成功后把编排同步为受控 Jenkins Job；Jenkins 不可达只标记，不阻塞发布。"""
    from .jenkins import JenkinsError
    from .provisioning import sync_pipeline_job
    try:
        sync_result = sync_pipeline_job(session, actor.id, definition, version)
    except JenkinsError:
        sync_result = {'jobRef': definition.job_ref, 'state': definition.job_sync_state or 'failed',
                       'action': 'deferred'}
    return {'id': definition.id, 'pipelineVersionId': version.id, 'contentHash': version.content_hash,
            'jobSync': sync_result}


def pipeline_xml(pid: str, name: str, version: str, job: str, arch: str) -> str:
    """按契约生成声明式配置：受控 Job、独占占用、固定参数模板与超时。"""
    return (
        f'<pipeline id="{pid}" name="{name}" version="{version}">\n'
        f'  <jenkins connection="jenkins-main" job="{job}" />\n'
        '  <environment>\n'
        '    <requirements>\n'
        f'      <item name="architecture" value="{arch}" />\n'
        '    </requirements>\n'
        '    <occupancy mode="exclusive" />\n'
        '  </environment>\n'
        '  <parameters>\n'
        '    <parameter name="IMAGE_REF" type="string" required="true" />\n'
        '    <parameter name="TEST_SUITE" type="enum" required="true" default="smoke">\n'
        '      <option value="smoke" />\n'
        '      <option value="network" />\n'
        '    </parameter>\n'
        '  </parameters>\n'
        '  <execution timeoutMinutes="120" />\n'
        '</pipeline>'
    )
