"""动态流水线编排：已发布 DAG → Jenkins Freestyle Job 配置，经 Jenkins API 创建/更新。

- 配置全部由 Argus 生成（ElementTree 构造，天然防外部实体注入），不导入外部 XML。
- Job 名 = 受控前缀 + 定义 ID 小写，确定性可重建；仅前缀内 Job 可写。
- 同步幂等：远端 config.xml 与本地生成一致即跳过；不一致则更新；不存在则创建。
- Jenkins 不可达不阻塞发布治理：job_sync_state 标记 pending/failed，由后台对账重试。
"""
from __future__ import annotations

import hashlib
from xml.etree import ElementTree as ET

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..errors import BusinessError
from ..models import PipelineDefinition, PipelineVersion
from . import WORKSPACE_ID, get_user, record_event
from .jenkins import JenkinsClient, JenkinsError, connection_config, job_name_for
from .pipelines import draft_content, topological_order

_INSTALL_SLEEP = 1   # 演示安装步时长（秒）
_CASE_SLEEP = 2      # 演示用例步时长（秒）


def job_name(definition_id: str) -> str:
    return job_name_for(settings().jenkins_job_prefix, definition_id)


def _sub(parent: ET.Element, tag: str, text: str | None = None) -> ET.Element:
    element = ET.SubElement(parent, tag)
    element.text = text
    return element


def _string_param(parent: ET.Element, name: str, description: str, default: str = "") -> None:
    prop = _sub(parent, "hudson.model.StringParameterDefinition")
    _sub(prop, "name", name)
    _sub(prop, "description", description)
    _sub(prop, "defaultValue", default)
    _sub(prop, "trim", "false")


def _choice_param(parent: ET.Element, name: str, description: str, choices: list[str]) -> None:
    prop = _sub(parent, "hudson.model.ChoiceParameterDefinition")
    _sub(prop, "name", name)
    _sub(prop, "description", description)
    choices_el = ET.SubElement(prop, "choices")
    choices_el.set("class", "java.util.Arrays$ArrayList")
    array_el = ET.SubElement(choices_el, "a")
    array_el.set("class", "string-array")
    for choice in choices:
        _sub(array_el, "string", choice)


def _shell_step(command: str) -> ET.Element:
    step = ET.Element("hudson.tasks.Shell")
    _sub(step, "command", command)
    return step


def _node_command(node: dict) -> str:
    """单个 DAG 节点 → 一段演示 shell（不运行真实任务，仅体现编排语义与可观测输出）。"""
    lines = ["set -e"]
    if node["type"] == "install":
        lines += [
            'echo "[install] node=%s env=$ENV_ID image=$IMAGE_REF"' % node["name"],
            f"sleep {_INSTALL_SLEEP}",
        ]
    else:
        cases = node["config"]["caseRefs"]
        lines.append('echo "[cases] node=%s total=%d mode=%s failure=%s"' % (
            node["name"], len(cases), node["config"]["mode"], node["config"]["failure"]))
        for case in cases:
            lines.append('echo "PASS case=%s version=%s"' % (case["id"], case["version"]))
        lines.append(f"sleep {_CASE_SLEEP}")
    return "\n".join(lines)


def generate_job_xml(definition: PipelineDefinition, version: PipelineVersion, graph: dict) -> bytes:
    """已发布编排 → Jenkins Freestyle config.xml（参数化 + 按 DAG 拓扑序的演示构建步骤）。"""
    root = ET.Element("project")
    _sub(root, "description",
         f"Argus 动态流水线 {definition.name} @{version.version} "
         f"(definition={definition.id} hash={version.content_hash[:12]})")
    _sub(root, "keepDependencies", "false")
    _sub(root, "quietPeriod", "0")
    _sub(root, "concurrentBuild", "false")
    properties = _sub(root, "properties")
    params = _sub(properties, "hudson.model.ParametersDefinitionProperty")
    definitions = _sub(params, "parameterDefinitions")
    _string_param(definitions, "IMAGE_REF", "环境镜像引用（运行参数快照固化）")
    _choice_param(definitions, "TEST_SUITE", "测试套件", ["smoke", "network"])
    _string_param(definitions, "ENV_ID", "执行环境 ID（独占占用边界）")
    _string_param(definitions, "CASES", "用例清单 JSON（Argus 用例目录引用快照）", "[]")
    _string_param(definitions, "ARGUS_RUN_ID", "Argus 运行 ID（对账关联）")
    _string_param(definitions, "ARGUS_TOKEN", "Argus 对账令牌（提交幂等标识）")
    _string_param(definitions, "ARGUS_CONTENT_HASH", "编排版本内容摘要（动态流水线版本对账）")
    builders = _sub(root, "builders")
    for node_id in topological_order(graph):
        node = next(n for n in graph["nodes"] if n["id"] == node_id)
        builders.append(_shell_step(_node_command(node)))
    _sub(root, "publishers")
    return ET.tostring(root, encoding="UTF-8", xml_declaration=True)


def content_fingerprint(xml: bytes) -> str:
    return hashlib.sha256(xml).hexdigest()


def job_xml_signature(xml: bytes) -> tuple:
    """Job 配置的语义签名：Jenkins 保存会经 XStream 重新排版，幂等比对必须忽略字节差异。

    覆盖发布治理关心的部分：描述、参数定义（名称/类型/选项）、按拓扑序的构建步骤命令。
    """
    root = ET.fromstring(xml)
    params = []
    for element in root.iter("hudson.model.StringParameterDefinition"):
        params.append(("string", element.findtext("name") or ""))
    for element in root.iter("hudson.model.ChoiceParameterDefinition"):
        choices = tuple(c.text or "" for c in element.iter("string"))
        params.append(("choice", element.findtext("name") or "", choices))
    commands = tuple(e.findtext("command") or "" for e in root.iter("hudson.tasks.Shell"))
    return (root.tag, root.findtext("description") or "", tuple(params), commands)


def _client() -> JenkinsClient | None:
    config = connection_config()
    return JenkinsClient(config) if config else None


def sync_pipeline_job(session: Session, actor_id: str, definition: PipelineDefinition,
                      version: PipelineVersion) -> dict:
    """把已发布编排同步为 Jenkins Job：创建/更新/跳过幂等；失败置 failed 交由对账重试。"""
    content = draft_content(version)
    graph = content.get("graph")
    if not graph or not graph.get("nodes"):
        raise BusinessError("已发布版本缺少 DAG 编排内容，无法生成动态流水线。")
    definition.job_ref = job_name(definition.id)
    client = _client()
    if client is None:
        definition.job_sync_state = "pending"
        return {"jobRef": definition.job_ref, "state": "pending", "action": "skipped-mock"}
    xml = generate_job_xml(definition, version, graph)
    fingerprint = content_fingerprint(xml)
    try:
        remote = client.get_job_config(definition.job_ref)
        if remote is None:
            client.create_job(definition.job_ref, xml)
            action = "created"
        elif job_xml_signature(remote) != job_xml_signature(xml):
            client.update_job(definition.job_ref, xml)
            action = "updated"
        else:
            action = "unchanged"
        definition.job_sync_state = "synced"
        definition.job_synced_hash = fingerprint
    except JenkinsError:
        definition.job_sync_state = "failed"
        raise
    actor = get_user(session, actor_id)
    record_event(session, actor_id=actor.id, action="pipeline_job_sync",
                 entity_type="pipeline", entity_id=definition.id,
                 payload={"text": f"{definition.name} 的 Jenkins Job 已同步（{action}）",
                          "jobRef": definition.job_ref, "action": action,
                          "fingerprint": fingerprint})
    return {"jobRef": definition.job_ref, "state": "synced", "action": action,
            "fingerprint": fingerprint}


def sync_published_job(session: Session, actor_id: str, pipeline_id: str) -> dict:
    """按定义 ID 同步其已发布版本的 Jenkins Job（REST/MCP 共用，管理员）。"""
    from . import require_admin
    actor = require_admin(session, actor_id, "同步流水线编排到 Jenkins")
    definition = session.execute(
        select(PipelineDefinition).where(PipelineDefinition.id == pipeline_id,
                                         PipelineDefinition.workspace_id == WORKSPACE_ID)).scalar_one_or_none()
    if definition is None:
        raise BusinessError(f"流水线 {pipeline_id} 不存在。")
    version = session.execute(
        select(PipelineVersion).where(PipelineVersion.definition_id == definition.id,
                                      PipelineVersion.status == "published")).scalar_one_or_none()
    if version is None:
        raise BusinessError(f"流水线 {pipeline_id} 没有已发布版本。")
    return sync_pipeline_job(session, actor.id, definition, version)


def resync_outdated_jobs(session: Session, actor_id: str) -> list[str]:
    """后台对账入口：重试所有 published 且同步态非 synced 的流水线。返回已恢复的 Job 名。"""
    rows = session.execute(
        select(PipelineDefinition).where(PipelineDefinition.workspace_id == WORKSPACE_ID)).scalars().all()
    recovered = []
    for definition in rows:
        if definition.job_sync_state == "synced":
            continue
        version = session.execute(
            select(PipelineVersion).where(PipelineVersion.definition_id == definition.id,
                                          PipelineVersion.status == "published")).scalar_one_or_none()
        if version is None:
            continue
        try:
            sync_pipeline_job(session, actor_id, definition, version)
        except (JenkinsError, BusinessError):
            continue  # 网络问题等待下轮对账；旧版声明式配置无法生成动态 Job，跳过
        if definition.job_sync_state == "synced":
            recovered.append(definition.job_ref)
    return recovered
