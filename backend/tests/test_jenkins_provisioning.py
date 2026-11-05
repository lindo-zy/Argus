"""Jenkins 动态流水线编排与调度验证（内存 Fake Jenkins，打在 HTTP 传输层）。

覆盖：配置生成与转义、受控前缀约束、发布→Job 同步（创建/更新/幂等/降级）、
运行提交幂等（至多一次）、queue→build→终态对账、队列取消、未知结果找回、
Jenkins 不可达时的降级与后台恢复、REST 对账入口。
"""
from __future__ import annotations

import json
from urllib.parse import parse_qs, unquote, urlencode, urlparse
from xml.etree import ElementTree as ET

import pytest
from sqlalchemy import select

from app.models import LeaseResource, PipelineDefinition, PipelineExecution, PipelineRun, PipelineVersion
from app.services import pipelines as pipeline_service
from app.services import runs as run_service
from app.services.jenkins import JenkinsClient, JenkinsError
from app.services.provisioning import generate_job_xml, job_name, sync_pipeline_job

from conftest import expect_business_error

JENKINS_BASE = "http://jenkins.test"


def graph_for(env_id: str) -> dict:
    return {"schema": 1, "nodes": [
        {"id": "install", "name": "环境安装", "type": "install", "position": {"x": 70, "y": 160},
         "config": {"envId": env_id, "imageRef": "os-24.03-rc2-arm64.img"}},
        {"id": "cases", "name": "用例执行<带转义>", "type": "cases", "position": {"x": 360, "y": 160},
         "config": {"caseRefs": [{"id": "PERF-001", "version": "v1"}, {"id": "PERF-002", "version": "v1"}],
                    "mode": "sequential", "failure": "stop"}},
    ], "edges": [{"id": "install-cases", "source": "install", "target": "cases"}]}


def create_published_pipeline(session, arch: str = "ARM64", env_id: str = "ENV-ARM-01",
                              name: str = "验证流水线") -> dict:
    saved = pipeline_service.save_pipeline(session, "U-1001", {
        "name": name, "description": "测试", "arch": arch, "graph": graph_for(env_id)})
    published = pipeline_service.publish_pipeline(session, "U-1001", saved["id"], {
        "expectedRevision": saved["editRevision"], "contentHash": saved["contentHash"]})
    session.flush()
    return published


class FakeJenkins:
    """内存 Jenkins：按 URL 路由模拟 Remote API 的最小行为。"""

    def __init__(self):
        self.jobs: dict[str, bytes] = {}
        self.builds: dict[tuple[str, str], dict] = {}
        self.queue: dict[str, dict] = {}
        self.submitted: list[tuple[str, dict]] = []
        self.next_queue, self.next_build = 100, 1
        self.fail_submit = False

    def _params(self, item: dict) -> list[dict]:
        return [{"name": k, "value": v} for k, v in item.get("params", {}).items()]

    @staticmethod
    def _normalize(data: bytes) -> bytes:
        """模拟 Jenkins 保存时 XStream 重新排版（缩进规范化），字节必变、语义不变。"""
        root = ET.fromstring(data)
        ET.indent(root)
        return ET.tostring(root, encoding="UTF-8", xml_declaration=True)

    def request(self, path: str, data=None, content_type=None):
        # 融合版客户端对表单提交传 dict，此处统一折算为编码后的键值对。
        if isinstance(data, dict):
            data = urlencode(data).encode()
        parsed = urlparse(path)
        parts = [unquote(p) for p in parsed.path.split("/") if p]
        query = parse_qs(parsed.query)
        if parsed.path == "/queue/api/json":
            items = [{"id": qid, "actions": [{"parameters": self._params(item)}],
                      "task": {"url": f"{JENKINS_BASE}/job/x/"}} for qid, item in self.queue.items()]
            return 200, {}, json.dumps({"items": items}).encode()
        if parts[:1] == ["createItem"]:
            self.jobs[query["name"][0]] = self._normalize(data)
            return 200, {}, b""
        if parts[:1] == ["job"]:
            job = parts[1]
            if len(parts) == 3 and parts[2] == "config.xml":
                if data is not None:
                    if job not in self.jobs:
                        return 404, {}, b"{}"
                    self.jobs[job] = self._normalize(data)
                    return 200, {}, b""
                raw = self.jobs.get(job)
                return (200, {}, raw) if raw is not None else (404, {}, b"{}")
            if len(parts) == 3 and parts[2] == "buildWithParameters":
                params = dict(pair.decode().split("=", 1) for pair in data.split(b"&"))
                self.submitted.append((job, params))
                if self.fail_submit:
                    raise JenkinsError("Jenkins 网络异常，等待对账。")
                qid = str(self.next_queue)
                self.next_queue += 1
                self.queue[qid] = {"params": params}
                return 201, {"Location": f"{JENKINS_BASE}/queue/item/{qid}/"}, b""
            if len(parts) == 4 and parts[2] == "api" and parts[3].startswith("json"):
                builds = [{"number": int(n), "actions": b.get("actions", [])}
                          for (j, n), b in self.builds.items() if j == job]
                return 200, {}, json.dumps({"builds": builds}).encode()
            if len(parts) == 5 and parts[2].isdigit() and parts[3] == "api" and parts[4].startswith("json"):
                build = self.builds.get((job, parts[2]))
                if build is None:
                    return 404, {}, b"{}"
                return 200, {}, json.dumps(build).encode()
        if parts[:2] == ["queue", "item"] and len(parts) >= 4 and parts[2].isdigit():
            item = self.queue.get(parts[2])
            if item is None:
                return 404, {}, b"{}"
            return 200, {}, json.dumps(item).encode()
        return 404, {}, b"{}"

    def put_build(self, job: str, number: str, params: dict, building: bool, result: str | None) -> None:
        self.builds[(job, number)] = {
            "building": building, "result": result,
            "actions": [{"parameters": self._params({"params": params})}]}


@pytest.fixture()
def fake_jenkins(monkeypatch):
    fake = FakeJenkins()
    client = JenkinsClient({"url": JENKINS_BASE, "prefix": "argus-",
                            "jobs": {"install": "argus-install", "cases": "argus-cases"}, "kind": "jenkins"})

    def fake_request(self, path, data=None, content_type="application/x-www-form-urlencoded"):
        return fake.request(path, data, content_type)

    monkeypatch.setattr(JenkinsClient, "request", fake_request)
    import app.services.provisioning as prov_mod
    import app.services.runs as runs_mod
    monkeypatch.setattr(runs_mod, "jenkins_client", lambda: client)
    monkeypatch.setattr(prov_mod, "_client", lambda: client)
    return fake


def active_devices(session) -> int:
    return len(session.execute(select(LeaseResource).where(LeaseResource.active.is_(True))).scalars().all())


# ---------- 配置生成 ----------

def test_generate_job_xml_shape_and_escaping(session):
    published = create_published_pipeline(session)
    definition = session.get(PipelineDefinition, published["id"])
    version = session.get(PipelineVersion, published["pipelineVersionId"])
    from app.services.pipelines import draft_content
    xml = generate_job_xml(definition, version, draft_content(version)["graph"])
    root = ET.fromstring(xml)
    assert root.tag == "project"
    param_names = {e.find("name").text for e in root.iter("hudson.model.StringParameterDefinition")}
    assert {"IMAGE_REF", "ENV_ID", "CASES", "ARGUS_TOKEN", "ARGUS_RUN_ID"} <= param_names
    commands = [e.find("command").text for e in root.iter("hudson.tasks.Shell")]
    assert len(commands) == 2  # install + cases，按拓扑序
    assert "env=$ENV_ID" in commands[0] and "image=$IMAGE_REF" in commands[0]  # 镜像经参数注入
    assert "用例执行<带转义>" in commands[1]  # XML 转义后可无损还原
    assert "PERF-001" in commands[1] and "PERF-002" in commands[1]


def test_job_name_is_deterministic_and_guarded(session):
    published = create_published_pipeline(session)
    assert job_name(published["id"]) == "argus-" + published["id"].lower()
    client = JenkinsClient({"url": JENKINS_BASE, "prefix": "argus-",
                            "jobs": {"install": "argus-install", "cases": "argus-cases"}, "kind": "jenkins"})
    with pytest.raises(JenkinsError):
        client.job_path("not-argus-job")
    with pytest.raises(JenkinsError):
        client.build_url("argus-x/..%2Fevil", "1")


# ---------- 发布同步 ----------

def test_publish_creates_job_via_api_and_is_idempotent(session, fake_jenkins):
    published = create_published_pipeline(session)
    definition = session.get(PipelineDefinition, published["id"])
    assert definition.job_ref == job_name(published["id"])
    assert definition.job_sync_state == "synced"
    stored = fake_jenkins.jobs[definition.job_ref]
    assert b"IMAGE_REF" in stored

    version = session.get(PipelineVersion, published["pipelineVersionId"])
    result = sync_pipeline_job(session, "U-1001", definition, version)
    assert result["action"] == "unchanged"  # 配置一致 → 跳过，不重复创建
    assert definition.job_sync_state == "synced"


def test_publish_survives_jenkins_outage_then_worker_recovers(session, monkeypatch):
    import app.services.provisioning as prov_mod

    class DownClient:
        def get_job_config(self, job):
            raise JenkinsError("Jenkins 网络异常，等待对账。")

    monkeypatch.setattr(prov_mod, "_client", lambda: DownClient())
    published = create_published_pipeline(session)
    definition = session.get(PipelineDefinition, published["id"])
    assert definition.job_sync_state == "failed"  # 发布不因 Jenkins 不可达而失败

    fake = FakeJenkins()
    client = JenkinsClient({"url": JENKINS_BASE, "prefix": "argus-",
                            "jobs": {"install": "argus-install", "cases": "argus-cases"}, "kind": "jenkins"})
    monkeypatch.setattr(JenkinsClient, "request",
                        lambda self, path, data=None, content_type="": fake.request(path, data, content_type))
    monkeypatch.setattr(prov_mod, "_client", lambda: client)
    recovered = prov_mod.resync_outdated_jobs(session, "U-1001")
    assert recovered == [definition.job_ref]
    assert definition.job_sync_state == "synced"
    assert definition.job_ref in fake.jobs


# ---------- 调度与对账 ----------

def test_start_run_submits_once_and_reconciles_to_terminal(session, fake_jenkins):
    published = create_published_pipeline(session)
    baseline = active_devices(session)
    run = run_service.start_run(session, "U-1002", {"pipelineVersionId": published["pipelineVersionId"],
                                                    "envId": "ENV-ARM-01"})
    assert active_devices(session) == baseline + 4  # ENV-ARM-01 的 4 台设备被独占
    assert run["status"] == "running" and run["queueId"].isdigit()
    assert run["jobRef"] == job_name(published["id"])
    assert len(fake_jenkins.submitted) == 1
    execution = session.get(PipelineExecution, run["id"])
    assert execution.snapshot["submitted"] is True
    assert execution.snapshot["params"]["ARGUS_TOKEN"]

    # queue → build
    qid = run["queueId"]
    params = fake_jenkins.queue[qid]["params"]
    number = str(fake_jenkins.next_build)
    fake_jenkins.next_build += 1
    fake_jenkins.queue.pop(qid)
    fake_jenkins.put_build(run["jobRef"], number, params, building=True, result=None)
    result = run_service.reconcile_run(session, session.get(PipelineRun, run["id"]))
    assert result["build"] == number and result["status"] == "running"

    # build → 终态 → 自动清理释放
    fake_jenkins.put_build(run["jobRef"], number, params, building=False, result="SUCCESS")
    result = run_service.reconcile_run(session, session.get(PipelineRun, run["id"]))
    assert result["status"] == "success" and result["cleanupState"] == "done"
    assert result["passed"] == result["total"] == 2
    assert result["consoleUrl"].startswith(JENKINS_BASE)
    assert active_devices(session) == baseline  # 设备全部释放，回到基线


def test_submit_never_retried_after_unknown_outcome(session, fake_jenkins):
    published = create_published_pipeline(session)
    baseline = active_devices(session)
    fake_jenkins.fail_submit = True
    run = run_service.start_run(session, "U-1002", {"pipelineVersionId": published["pipelineVersionId"],
                                                    "envId": "ENV-ARM-01"})
    assert run["status"] == "reconciling"  # 结果不明 → 等待对账
    assert len(fake_jenkins.submitted) == 1

    # 恢复网络：模拟提交实际已到达（build 已存在），按 token 找回
    execution = session.get(PipelineExecution, run["id"])
    params = execution.snapshot["params"]
    number = str(fake_jenkins.next_build)
    fake_jenkins.next_build += 1
    fake_jenkins.put_build(run["jobRef"], number, params, building=True, result=None)
    fake_jenkins.fail_submit = False
    result = run_service.reconcile_run(session, session.get(PipelineRun, run["id"]))
    assert result["build"] == number and result["status"] == "running"
    assert len(fake_jenkins.submitted) == 1  # 绝不重复提交


def test_queue_cancelled_maps_to_failed(session, fake_jenkins):
    published = create_published_pipeline(session)
    baseline = active_devices(session)
    run = run_service.start_run(session, "U-1002", {"pipelineVersionId": published["pipelineVersionId"],
                                                    "envId": "ENV-ARM-01"})
    fake_jenkins.queue[run["queueId"]]["cancelled"] = True
    result = run_service.reconcile_run(session, session.get(PipelineRun, run["id"]))
    assert result["status"] == "failed" and result["result"] == "failed"
    assert result["cleanupState"] == "done"
    assert active_devices(session) == baseline


def test_reconcile_endpoint_reports_counts(session, client, fake_jenkins):
    published = create_published_pipeline(session)
    run_service.start_run(session, "U-1002", {"pipelineVersionId": published["pipelineVersionId"],
                                              "envId": "ENV-ARM-01"})
    response = client.post("/api/runs/reconcile")
    assert response.status_code == 200
    body = response.json()["result"]
    assert body["checked"] >= 1


def test_mock_mode_execution_untouched(session):
    published = create_published_pipeline(session)
    run = run_service.start_run(session, "U-1002", {"pipelineVersionId": published["pipelineVersionId"],
                                                    "envId": "ENV-ARM-01"})
    assert run["jobRef"] == job_name(published["id"])  # 发布已固化受控 Job 名
    assert run["build"] == "#mock" and run["status"] == "running"  # 执行仍为演示模拟
    assert session.get(PipelineExecution, run["id"]) is None


def test_start_run_rejects_unpublished_version(session, fake_jenkins):
    expect_business_error(
        run_service.start_run, session, "U-1002",
        {"pipelineVersionId": "PL-nonexistent@v1", "envId": "ENV-ARM-01"},
        match="只有已发布配置可以启动任务")
