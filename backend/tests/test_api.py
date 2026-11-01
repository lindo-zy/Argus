"""REST 层测试：X-Argus-User 透传、{detail, code} 错误形状、{result, snapshot} 变更返回。

使用 dependency_overrides 覆盖 get_session 指向 argus_test 会话；
不使用 TestClient 上下文管理器（跳过 startup 事件，避免触碰 app.db.SessionLocal）。
"""
from __future__ import annotations

from app.services import leases as lease_service

ADMIN = {"X-Argus-User": "U-1001"}
MEMBER = {"X-Argus-User": "U-1003"}


class TestIdentityAndErrors:
    def test_missing_or_unknown_actor_returns_401_shape(self, client):
        resp = client.post("/api/leases/batch", json={
            "envIds": ["ENV-ARM-01"], "hours": 4, "purpose": "匿名"})
        assert resp.status_code == 401
        body = resp.json()
        assert set(body) == {"detail", "code"}
        assert body["code"] == "unknown_actor"

        resp = client.post("/api/leases/batch", json={
            "envIds": ["ENV-ARM-01"], "hours": 4, "purpose": "黑户"},
            headers={"X-Argus-User": "U-9999"})
        assert resp.status_code == 401
        assert resp.json()["code"] == "unknown_actor"

    def test_member_forbidden_returns_403_shape(self, client):
        resp = client.post("/api/environments/ENV-ARM-01/retire", headers=MEMBER)
        assert resp.status_code == 403
        body = resp.json()
        assert set(body) == {"detail", "code"}
        assert body["code"] == "permission_denied"
        assert "仅管理员" in body["detail"]

    def test_business_rule_returns_400_shape(self, client):
        resp = client.post("/api/leases/batch", json={
            "envIds": ["ENV-ARM-01"], "hours": 3, "purpose": "非法时长"}, headers=ADMIN)
        assert resp.status_code == 400
        body = resp.json()
        assert set(body) == {"detail", "code"}
        assert body["code"] == "business_rule"
        assert "请选择有效的借用时长" in body["detail"]

    def test_disabled_actor_400(self, client):
        client.post("/api/users/U-1004/status", json={"status": "disabled"}, headers=ADMIN)
        resp = client.post("/api/leases/batch", json={
            "envIds": ["ENV-ARM-01"], "hours": 4, "purpose": "停用后"},
            headers={"X-Argus-User": "U-1004"})
        assert resp.status_code == 400
        assert resp.json()["code"] == "actor_disabled"


class TestBorrowEndpoint:
    def test_batch_borrow_returns_result_and_snapshot(self, client):
        resp = client.post("/api/leases/batch", json={
            "envIds": ["ENV-ARM-01", "ENV-VM-03"], "hours": 4, "purpose": "REST 借用"},
            headers=ADMIN)
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == {"result", "snapshot"}
        result = body["result"]
        assert result["lease"]["envIds"] == ["ENV-ARM-01", "ENV-VM-03"]
        assert result["lease"]["user"] == "王磊"
        snapshot = body["snapshot"]
        assert snapshot["schema"] == 3
        assert any(l["id"] == result["lease"]["id"] for l in snapshot["leases"])
        assert any(e["id"] == "ENV-ARM-01" and e["status"] == "leased"
                   for e in snapshot["environments"])

    def test_batch_borrow_request_key_header_idempotent(self, client):
        payload = {"envIds": ["ENV-VM-03"], "hours": 2, "purpose": "幂等头"}
        first = client.post("/api/leases/batch", json=payload, headers={
            **ADMIN, "X-Argus-Request-Key": "rest-key-1"})
        second = client.post("/api/leases/batch", json=payload, headers={
            **ADMIN, "X-Argus-Request-Key": "rest-key-1"})
        assert first.status_code == 200 and second.status_code == 200
        assert second.json()["result"] == first.json()["result"]
        assert second.json()["result"]["lease"]["id"] == first.json()["result"]["lease"]["id"]

    def test_request_key_conflict_returns_400(self, client):
        first = client.post("/api/leases/batch", json={
            "envIds": ["ENV-VM-03"], "hours": 2, "purpose": "冲突键"},
            headers={**ADMIN, "X-Argus-Request-Key": "rest-key-2"})
        assert first.status_code == 200
        second = client.post("/api/leases/batch", json={
            "envIds": ["ENV-ARM-01"], "hours": 2, "purpose": "冲突键"},
            headers={**ADMIN, "X-Argus-Request-Key": "rest-key-2"})
        assert second.status_code == 400
        assert second.json()["code"] == "request_conflict"

    def test_partial_success_outcomes_via_rest(self, client):
        resp = client.post("/api/leases/batch", json={
            "envIds": ["ENV-X86-02", "ENV-VM-03"], "hours": 8, "purpose": "部分成功"},
            headers=MEMBER)
        assert resp.status_code == 200
        outcomes = resp.json()["result"]["outcomes"]
        assert outcomes[0]["status"] == "skipped"
        assert outcomes[0]["code"] == "running"
        assert outcomes[1]["status"] == "borrowed"
        assert resp.json()["result"]["lease"]["user"] == "李四"

    def test_renew_return_verify_flow_via_rest(self, client):
        borrowed = client.post("/api/leases/batch", json={
            "envIds": ["ENV-VM-03"], "hours": 2, "purpose": "REST 生命周期"}, headers=MEMBER)
        lease_id = borrowed.json()["result"]["lease"]["id"]

        renewed = client.post(f"/api/leases/{lease_id}/renew", json={"hours": 4},
                              headers=MEMBER)
        assert renewed.status_code == 200
        assert renewed.json()["result"]["status"] == "active"

        pending = client.post(f"/api/leases/{lease_id}/return", headers=MEMBER)
        assert pending.json()["result"]["status"] == "checking"

        verified = client.post(f"/api/leases/{lease_id}/verify-return",
                               json={"healthy": True}, headers=MEMBER)
        assert verified.json()["result"]["status"] == "released"
        assert verified.json()["snapshot"]["schema"] == 3

    def test_own_lease_guard_via_rest(self, client):
        resp = client.post("/api/leases/LEASE-1001/renew", json={"hours": 4}, headers=MEMBER)
        assert resp.status_code == 400
        assert "只能操作自己的借用记录" in resp.json()["detail"]


class TestEnvironmentEndpoints:
    def test_create_environment_via_rest(self, client):
        resp = client.post("/api/environments", json={
            "name": "REST 环境", "arch": "ARM64", "type": "physical",
            "deviceIds": ["DEV-001"], "description": "REST 建档"}, headers=ADMIN)
        assert resp.status_code == 200
        body = resp.json()
        assert body["result"]["name"] == "REST 环境"
        assert any(e["id"] == body["result"]["id"] for e in body["snapshot"]["environments"])

    def test_create_environment_member_forbidden_via_rest(self, client):
        resp = client.post("/api/environments", json={
            "name": "REST 环境", "arch": "ARM64", "type": "physical",
            "deviceIds": ["DEV-001"], "description": "REST 建档"}, headers=MEMBER)
        assert resp.status_code == 403

    def test_list_environments_filters(self, client):
        data = client.get("/api/environments").json()["environments"]
        assert all(e["lifecycle"] == "active" for e in data)
        assert len(data) == 24

        with_retired = client.get(
            "/api/environments", params={"include_retired": "true"}).json()["environments"]
        assert len(with_retired) == 24  # seed 无 retired

        client.post("/api/environments/ENV-ARM-01/retire", headers=ADMIN)
        after = client.get("/api/environments").json()["environments"]
        assert all(e["id"] != "ENV-ARM-01" for e in after)
        restored = client.post("/api/environments/ENV-ARM-01/restore", headers=ADMIN)
        assert restored.status_code == 200
        assert any(e["id"] == "ENV-ARM-01"
                   for e in client.get("/api/environments").json()["environments"])

    def test_retire_restore_admin_guard(self, client):
        assert client.post("/api/environments/ENV-ARM-01/restore",
                           headers=MEMBER).status_code == 403

    def test_adjust_devices_endpoint(self, client):
        resp = client.post("/api/environments/ENV-ARM-01/devices",
                           json={"add": [], "remove": ["DEV-001"]}, headers=ADMIN)
        assert resp.status_code == 200
        assert set(resp.json()["result"]["deviceIds"]) == {"DEV-002", "DEV-003", "DEV-004"}

    def test_health_check_endpoint(self, client):
        resp = client.post("/api/environments/ENV-ARM-01/health-check",
                           json={"healthy": True, "note": "REST 巡检"}, headers=ADMIN)
        assert resp.status_code == 200
        assert resp.json()["result"]["healthy"] is True


class TestMaintenanceAndRuns:
    def test_maintenance_start_and_verify(self, client):
        started = client.post("/api/maintenance", json={
            "envId": "ENV-ARM-01", "reason": "REST 维护"}, headers=ADMIN)
        assert started.status_code == 200
        record_id = started.json()["result"]["id"]
        assert any(m["id"] == record_id and m["status"] == "maintenance"
                   for m in started.json()["snapshot"]["maintenance"])
        verified = client.post(f"/api/maintenance/{record_id}/verify",
                               json={"healthy": True}, headers=ADMIN)
        assert verified.json()["result"]["status"] == "success"

    def test_maintenance_start_rejects_busy(self, client):
        resp = client.post("/api/maintenance", json={
            "envId": "ENV-X86-02", "reason": "不该成功"}, headers=ADMIN)
        assert resp.status_code == 400
        assert "只能对空闲环境开始维护" in resp.json()["detail"]

    def test_run_lifecycle_via_rest(self, client):
        started = client.post("/api/runs", json={
            "pipelineVersionId": "os-smoke-test@v3", "envId": "ENV-VM-03"}, headers=MEMBER)
        assert started.status_code == 200
        run_id = started.json()["result"]["id"]
        assert started.json()["result"]["status"] == "running"

        finished = client.post(f"/api/runs/{run_id}/finish", json={"success": True},
                               headers=MEMBER)
        assert finished.json()["result"]["status"] == "cleaning"

        cleaned = client.post(f"/api/runs/{run_id}/cleanup", json={"healthy": True},
                              headers=MEMBER)
        assert cleaned.json()["result"]["status"] == "success"
        assert cleaned.json()["result"]["cleanupState"] == "done"

    def test_run_draft_rejected_via_rest(self, client):
        resp = client.post("/api/runs", json={
            "pipelineVersionId": "storage-chaos@v1", "envId": "ENV-ARM-01"}, headers=ADMIN)
        assert resp.status_code == 400
        assert "只有已发布配置可以启动任务" in resp.json()["detail"]


class TestUsersAndQueries:
    def test_list_users(self, client):
        users = client.get("/api/users").json()["users"]
        assert len(users) == 4
        assert {u["id"] for u in users} == {"U-1001", "U-1002", "U-1003", "U-1004"}

    def test_add_user_admin_only(self, client):
        assert client.post("/api/users", json={"name": "赵倩"},
                           headers=MEMBER).status_code == 403
        ok = client.post("/api/users", json={"name": "赵倩", "role": "member"},
                         headers=ADMIN)
        assert ok.status_code == 200
        assert ok.json()["result"]["id"] == "U-2001"
        assert any(u["name"] == "赵倩" for u in ok.json()["snapshot"]["users"])

    def test_role_and_status_endpoints(self, client):
        role = client.post("/api/users/U-1002/role", json={"role": "admin"}, headers=ADMIN)
        assert role.json()["result"]["role"] == "admin"
        self_guard = client.post("/api/users/U-1001/status", json={"status": "disabled"},
                                 headers=ADMIN)
        assert self_guard.status_code == 400
        assert "不能停用或启用当前身份自己" in self_guard.json()["detail"]

    def test_events_endpoint(self, client):
        client.post("/api/leases/batch", json={
            "envIds": ["ENV-ARM-01"], "hours": 4, "purpose": "事件查询"}, headers=ADMIN)
        events = client.get("/api/events", params={"limit": 50}).json()["events"]
        assert any(e["action"] == "batch_borrow_result" for e in events)
        assert all({"id", "action", "entityType", "actor", "text", "occurredAt"} <= set(e)
                   for e in events)

    def test_report_endpoints(self, client):
        dist = client.get("/api/reports/distribution").json()
        assert dist["devices"]["total"] == 58
        flow = client.get("/api/reports/status-flow").json()
        assert "counts" in flow
        util = client.get("/api/environments/ENV-ARM-01/utilization").json()
        assert util["envId"] == "ENV-ARM-01"
        assert 0 <= util["utilization"] <= 100
        timeline = client.get("/api/environments/ENV-ARM-01/timeline").json()
        assert timeline["envId"] == "ENV-ARM-01"
        assert "items" in timeline

    def test_snapshot_endpoint(self, client):
        resp = client.get("/api/snapshot")
        assert resp.status_code == 200
        snap = resp.json()
        assert snap["schema"] == 3
        assert len(snap["users"]) == 4
        assert "revision" in snap

    def test_export_csv(self, client):
        resp = client.get("/api/export/environments.csv")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/csv")
        lines = resp.text.strip().splitlines()
        assert lines[0].startswith("ID")
        assert lines[0].split(",")[2] == "IP"
        assert len(lines) == 25  # 表头 + 24 环境

    def test_reset_requires_admin(self, client):
        assert client.post("/api/reset", headers=MEMBER).status_code == 403
