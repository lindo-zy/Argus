#!/usr/bin/env python3
"""端到端验证：Argus 流水线编排 → Jenkins 动态流水线 → 调度与结果回同步。

前置：
- 本地 Jenkins（默认 http://127.0.0.1:18090，用户 argus + API Token）；
- Argus 后端验证实例（默认 http://127.0.0.1:8100，已配置 Jenkins 连接）；
- 环境变量：ARGUS_JENKINS_TOKEN（必填）、ARGUS_API_BASE / JENKINS_BASE（可选）。

流程：
1. 读取 Argus 用例目录，创建并发布 20 条流水线（发布即经 Jenkins API 创建受控 Job）；
2. Jenkins 侧核对 Job 数量与配置内容（参数、按 DAG 拓扑序的构建步骤）；
3. 按环境空闲情况分波调度 20 次运行，等待全部终态并核对回同步结果；
4. 动态对账演示：篡改远端 Job 配置 → Argus 重新同步恢复一致；
5. 输出验证摘要 JSON。
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

API = os.environ.get("ARGUS_API_BASE", "http://127.0.0.1:8100")
JENKINS = os.environ.get("JENKINS_BASE", "http://127.0.0.1:18090")
TOKEN = os.environ.get("ARGUS_JENKINS_TOKEN", "")
ADMIN = "U-1001"
PIPELINE_COUNT = 20

if not TOKEN:
    sys.exit("缺少 ARGUS_JENKINS_TOKEN 环境变量（Jenkins API Token）")


def http_json(url: str, payload: dict | None = None, headers: dict | None = None, method: str = ""):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method or ("POST" if data else "GET"),
                                     headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def api(path: str, payload: dict | None = None):
    return http_json(API + path, payload, {"X-Argus-User": ADMIN})


def jenkins(path: str, payload: bytes | None = None, content_type: str = ""):
    headers = {"Authorization": "Basic " + base64.b64encode(f"argus:{TOKEN}".encode()).decode()}
    if content_type:
        headers["Content-Type"] = content_type if "charset" in content_type else content_type + "; charset=utf-8"
    request = urllib.request.Request(JENKINS + path, data=payload, headers=headers,
                                     method="POST" if payload is not None else "GET")
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            raw = resp.read()
            return resp.status, raw
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def case_refs(catalog: list[dict], arch: str, count: int) -> list[dict]:
    """按架构从用例目录选取用例（与 Argus 校验同源）。"""
    matched = [c for c in catalog if arch in c["arches"]]
    if len(matched) < count:
        raise SystemExit(f"用例目录中 {arch} 可用用例不足 {count} 条")
    return [{"id": c["id"], "version": c["version"]} for c in matched[:count]]


def draft_payload(name: str, arch: str, env_id: str, cases: list[dict]) -> dict:
    return {
        "name": name, "description": "Jenkins 动态流水线端到端验证", "arch": arch,
        "graph": {
            "schema": 1,
            "nodes": [
                {"id": "install", "name": "环境安装", "type": "install",
                 "position": {"x": 80, "y": 160}, "config": {"envId": env_id, "imageRef": "os-24.03-rc2.img"}},
                {"id": "cases", "name": "用例执行", "type": "cases",
                 "position": {"x": 380, "y": 160},
                 "config": {"caseRefs": cases, "mode": "sequential", "failure": "stop"}},
            ],
            "edges": [{"id": "install-cases", "source": "install", "target": "cases"}],
        },
    }


def main() -> None:
    catalog_path = Path(__file__).resolve().parents[1] / "backend/app/data/pipeline_cases.json"
    catalog = json.loads(catalog_path.read_text())

    # ---- 0. 环境就绪检查 ----
    status, snapshot = api("/api/snapshot")
    assert status == 200, f"Argus API 不可达：{status}"
    available_envs = [e for e in snapshot["environments"] if e["status"] == "available"]
    arch_envs: dict[str, list[str]] = {}
    for env in available_envs:
        arch_envs.setdefault(env["arch"], []).append(env["id"])
    total_slots = sum(len(v) for v in arch_envs.values())
    print(f"[0] Argus 就绪；空闲环境 {total_slots} 个：{ {k: len(v) for k, v in arch_envs.items()} }")

    # ---- 1. 创建并发布 20 条流水线（发布即动态创建 Jenkins Job）----
    pipelines = []
    arch_cycle = (["x86_64", "ARM64"] if "x86_64" in arch_envs and "ARM64" in arch_envs
                  else list(arch_envs))
    for i in range(1, PIPELINE_COUNT + 1):
        arch = arch_cycle[i % len(arch_cycle)]
        env_id = arch_envs[arch][i % len(arch_envs[arch])]
        cases = case_refs(catalog, arch, 3)
        status, saved = api("/api/pipelines/drafts", draft_payload(
            f"Argus-Jenkins 验证流水线 {i:02d}", arch, env_id, cases))
        assert status == 200, f"保存草稿失败：{saved}"
        status, published = api(f"/api/pipelines/{saved['result']['id']}/publish", {
            "expectedRevision": saved["result"]["editRevision"],
            "contentHash": saved["result"]["contentHash"]})
        assert status == 200, f"发布失败：{published}"
        job_sync = published["result"]["jobSync"]
        assert job_sync["state"] == "synced", f"Job 同步未成功：{job_sync}"
        pipelines.append({"id": saved["result"]["id"], "name": f"Argus-Jenkins 验证流水线 {i:02d}",
                          "versionId": published["result"]["pipelineVersionId"],
                          "jobRef": job_sync["jobRef"], "envId": env_id, "arch": arch})
    print(f"[1] 已创建并发布 {len(pipelines)} 条流水线，全部经 Jenkins API 同步为受控 Job")

    # ---- 2. Jenkins 侧核对 ----
    status, raw = jenkins("/api/json?tree=jobs[name]")
    jenkins_jobs = {j["name"] for j in json.loads(raw)["jobs"]}
    argus_jobs = {p["jobRef"] for p in pipelines}
    missing = argus_jobs - jenkins_jobs
    assert not missing, f"Jenkins 缺少 Job：{missing}"
    status, raw = jenkins(f"/job/{pipelines[0]['jobRef']}/config.xml")
    config = raw.decode()
    assert "<name>IMAGE_REF</name>" in config and "<name>ARGUS_TOKEN</name>" in config
    assert "hudson.tasks.Shell" in config
    print(f"[2] Jenkins 现有 argus-* 受控 Job：{len([j for j in jenkins_jobs if j.startswith('argus-')])} 个，"
          f"配置含参数化定义与拓扑序构建步骤")

    # ---- 3. 分波调度 20 次运行 ----
    runs, wave, busy = [], 1, set()
    pending = list(pipelines)
    while pending:
        wave_runs = []
        for pipeline in list(pending):
            if pipeline["envId"] in busy:
                continue
            status, body = api("/api/runs", {"pipelineVersionId": pipeline["versionId"],
                                             "envId": pipeline["envId"]})
            if status != 200:
                busy.add(pipeline["envId"])  # 占用冲突：本波跳过，下波再试
                continue
            busy.add(pipeline["envId"])
            wave_runs.append({**pipeline, "runId": body["result"]["id"]})
            pending.remove(pipeline)
        if not wave_runs:
            busy.clear()  # 所有环境均忙：等待释放后重试
            continue
        print(f"[3.{wave}] 启动第 {wave} 波 {len(wave_runs)} 条运行")
        deadline = time.time() + 120
        while wave_runs and time.time() < deadline:
            api("/api/runs/reconcile")
            time.sleep(2)
            for item in list(wave_runs):
                status, body = api("/api/snapshot")
                run = next((r for r in body["runs"] if r["id"] == item["runId"]), None)
                assert run is not None, f"运行 {item['runId']} 不存在"
                if run["status"] in ("success", "failed"):
                    assert run["cleanupState"] == "done", f"{item['runId']} 清理未完成：{run}"
                    assert run["build"].isdigit(), f"{item['runId']} build 号未回填：{run}"
                    assert run["consoleUrl"].startswith(JENKINS), f"{item['runId']} 缺少控制台引用：{run}"
                    runs.append({**item, **run})
                    busy.discard(item["envId"])
                    wave_runs.remove(item)
        assert not wave_runs, f"第 {wave} 波存在未终态运行：{wave_runs}"
        wave += 1
    failed = [r for r in runs if r["status"] != "success"]
    assert not failed, f"存在失败运行：{failed}"
    print(f"[3] {len(runs)} 条运行全部 success / 清理完成 / build 号与控制台引用已回填")

    # ---- 4. 动态对账：远端漂移 → Argus 重新同步 ----
    target = pipelines[0]
    status, raw = jenkins(f"/job/{target['jobRef']}/config.xml")
    drifted = raw.replace(b"Argus", b"Drifted", 1)
    status, _ = jenkins(f"/job/{target['jobRef']}/config.xml", drifted, "application/xml")
    assert status == 200
    status, synced = api(f"/api/pipelines/{target['id']}/sync", {})
    assert status == 200 and synced["result"]["action"] == "updated", f"漂移恢复失败：{synced}"
    status, raw = jenkins(f"/job/{target['jobRef']}/config.xml")
    assert b"Drifted" not in raw, "远端配置未恢复"
    status, again = api(f"/api/pipelines/{target['id']}/sync", {})
    assert again["result"]["action"] == "unchanged", f"二次同步非幂等：{again}"
    print("[4] 远端配置漂移已被 Argus 检测并经 Jenkins API 恢复；重复同步幂等（unchanged）")

    # ---- 5. Jenkins 侧构建计数核对 ----
    build_count = 0
    for pipeline in pipelines:
        status, raw = jenkins(f"/job/{pipeline['jobRef']}/api/json?tree=builds[number,result]")
        build_count += len(json.loads(raw).get("builds", []))
    assert build_count >= PIPELINE_COUNT, f"Jenkins 构建数不足：{build_count}"
    summary = {
        "pipelines": len(pipelines),
        "jenkinsJobs": len(argus_jobs),
        "runs": len(runs),
        "builds": build_count,
        "successRuns": len([r for r in runs if r["status"] == "success"]),
        "waveCount": wave - 1,
    }
    print("[5] 端到端验证通过：" + json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
