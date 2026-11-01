"""快照形状与洞察报表测试：schema=3 派生抽检、timeline、utilization、distribution、status_flow。

规则来源 docs/02 状态派生口径、docs/07 快照结构；对齐前端 mock 快照。
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.models import Environment, LeaseResource, MaintenanceRecord
from app.services import environments as env_service
from app.services import leases as lease_service
from app.services import maintenance as maintenance_service
from app.services import runs as run_service
from app.services import snapshot as snapshot_service
from app.services.insights import (
    distribution, environment_timeline, environment_utilization, status_flow,
)

ADMIN = "U-1001"
MEMBER = "U-1003"


def snap(session):
    return snapshot_service.build_snapshot(session)


def env_by_id(snapshot, env_id):
    return next(e for e in snapshot["environments"] if e["id"] == env_id)


class TestSnapshotShape:
    def test_top_level_shape(self, session):
        s = snap(session)
        assert s["schema"] == 3
        assert isinstance(s["revision"], int) and s["revision"] >= 1
        assert len(s["users"]) == 4
        assert len(s["environments"]) == 24
        assert len(s["devices"]) == 58
        assert len(s["leases"]) == 9  # 4 手工 + 5 执行
        assert len(s["runs"]) == 8
        assert len(s["maintenance"]) == 3
        assert {u["role"] for u in s["users"]} == {"admin", "member"}
        assert [p["id"] for p in s["pipelines"]] == [
            "os-smoke-test", "arm-compatibility", "storage-chaos"]
        assert s["pipelines"][0]["pipelineVersionId"] == "os-smoke-test@v3"
        assert s["pipelines"][2]["status"] == "draft"
        assert len(s["issues"]) == 4
        assert isinstance(s["activities"], list) and 1 <= len(s["activities"]) <= 12

    def test_environment_status_derivation_samples(self, session):
        s = snap(session)
        assert env_by_id(s, "ENV-ST-04")["status"] == "maintenance"   # MT-003
        assert env_by_id(s, "ENV-ARM-05")["status"] == "leased"       # LEASE-1001 手工占用
        assert env_by_id(s, "ENV-009")["status"] == "isolated"        # MT-001
        assert env_by_id(s, "ENV-013")["status"] == "cleaning"        # RUN-1026
        assert env_by_id(s, "ENV-ARM-01")["status"] == "available"
        assert env_by_id(s, "ENV-X86-02")["status"] == "running"      # RUN-1028

    def test_device_and_lease_shapes(self, session):
        s = snap(session)
        device = next(d for d in s["devices"] if d["id"] == "DEV-014")
        assert device["status"] == "maintenance"  # 共享设备随 ENV-ST-04 维护
        lease = next(l for l in s["leases"] if l["id"] == "LEASE-1001")
        assert lease["envIds"] == ["ENV-ARM-05"]
        assert lease["user"] == "王磊"
        assert lease["kind"] == "manual"
        assert lease["status"] == "active"
        assert lease["deviceIds"] == ["DEV-018", "DEV-019", "DEV-020"]
        run = next(r for r in s["runs"] if r["id"] == "RUN-1028")
        assert run["status"] == "running"
        assert run["cleanupState"] == "pending"

    def test_snapshot_reflects_changes(self, session):
        before = snap(session)
        lease_service.borrow(session, MEMBER,
                             {"envIds": ["ENV-ARM-01"], "hours": 4, "purpose": "快照演进"})
        after = snap(session)
        assert after["revision"] > before["revision"]
        assert env_by_id(after, "ENV-ARM-01")["status"] == "leased"
        assert len(after["leases"]) == len(before["leases"]) + 1
        assert len(after["users"]) == len(before["users"])


class TestTimeline:
    def test_timeline_aggregates_lease_run_maintenance_event(self, session):
        created = env_service.save_environment(session, ADMIN, {
            "name": "时间线环境", "arch": "ARM64", "type": "physical",
            "deviceIds": ["DEV-002"]})
        env_id = created["id"]
        # 执行（跑完释放）→ 借用（归还释放）→ 维护，四类记录落在同一环境
        run = run_service.start_run(session, MEMBER, {
            "pipelineVersionId": "arm-compatibility@v2", "envId": env_id})
        run_service.finish_run(session, MEMBER, run["id"], success=True)
        run_service.cleanup_run(session, MEMBER, run["id"], healthy=True)
        borrowed = lease_service.borrow(session, MEMBER,
                                        {"envIds": [env_id], "hours": 4, "purpose": "时间线借用"})
        lease_service.request_return(session, MEMBER, borrowed["lease"]["id"])
        lease_service.verify_return(session, MEMBER, borrowed["lease"]["id"], healthy=True)
        maintenance_service.start(session, ADMIN, {"envId": env_id, "reason": "时间线维护"})
        session.flush()

        items = environment_timeline(session, env_id)
        kinds = {i["kind"] for i in items}
        assert {"lease", "run", "maintenance", "event"} <= kinds
        times = [i["time"] for i in items]
        assert times == sorted(times, reverse=True)  # 倒序
        lease_item = next(i for i in items if i["kind"] == "lease")
        assert "时间线借用" in lease_item["title"]
        run_item = next(i for i in items if i["kind"] == "run")
        assert run_item["ref"] == run["id"]
        event_item = next(i for i in items if i["kind"] == "event")
        assert event_item["ref"] == "environment_create"

    def test_timeline_empty_env(self, session):
        assert environment_timeline(session, "ENV-ARM-01") != []
        assert environment_timeline(session, "NOPE") == []


class TestUtilization:
    def test_utilization_numbers_and_history(self, session):
        result = lease_service.borrow(session, MEMBER,
                                      {"envIds": ["ENV-ARM-01"], "hours": 4, "purpose": "利用率"})
        lease_id = result["lease"]["id"]
        # 回填占用区间：3 小时前取得，1 小时前释放
        rows = session.execute(select(LeaseResource).where(
            LeaseResource.lease_id == lease_id)).scalars().all()
        now = rows[0].acquired_at
        for row in rows:
            row.acquired_at = now - timedelta(hours=3)
            row.released_at = now - timedelta(hours=1)
        session.flush()

        report = environment_utilization(session, "ENV-ARM-01", days=7)
        assert report["envId"] == "ENV-ARM-01"
        assert report["deviceCount"] == 4
        assert 0 <= report["utilization"] <= 100
        # 4 台设备 × 2 小时占用 = 8 设备小时
        assert abs(report["occupiedHours"] - 8.0) < 0.1
        assert len(report["history"]) == 4
        assert all(h["hours"] == 2.0 for h in report["history"])
        assert {h["leaseId"] for h in report["history"]} == {lease_id}
        assert all(h["kind"] == "manual" for h in report["history"])

    def test_utilization_active_lease_counts_until_now(self, session):
        result = lease_service.borrow(session, MEMBER,
                                      {"envIds": ["ENV-VM-03"], "hours": 4, "purpose": "进行中"})
        report = environment_utilization(session, "ENV-VM-03", days=7)
        # 刚借出：占用时长接近 0 但不为负
        assert 0 <= report["utilization"] <= 100
        assert report["occupiedHours"] >= 0
        assert len(report["history"]) == 3

    def test_utilization_excludes_maintenance_kind(self, session):
        maintenance_service.start(session, ADMIN, {"envId": "ENV-ARM-01", "reason": "维护不计占用"})
        report = environment_utilization(session, "ENV-ARM-01", days=7)
        assert report["history"] == []
        assert report["utilization"] == 0


class TestDistribution:
    def test_distribution_counts_sum_to_totals(self, session):
        report = distribution(session)
        devices = report["devices"]
        assert devices["total"] == 58
        assert sum(devices["byArch"].values()) == devices["total"]
        assert sum(devices["byType"].values()) == devices["total"]
        assert sum(devices["byLocation"].values()) == devices["total"]
        envs = report["environments"]
        assert envs["total"] == 24
        active = session.execute(select(Environment).where(
            Environment.lifecycle == "active")).scalars().all()
        assert envs["total"] == len(active)

    def test_distribution_reflects_retire(self, session):
        env_service.archive_environment(session, ADMIN, "ENV-ARM-01")
        report = distribution(session)
        assert report["environments"]["total"] == 23  # 设备未移出，仍 58
        assert report["devices"]["total"] == 58


class TestStatusFlow:
    def test_status_flow_counts_events(self, session):
        lease_service.borrow(session, MEMBER,
                             {"envIds": ["ENV-ARM-01"], "hours": 4, "purpose": "流转统计"})
        maintenance_service.start(session, ADMIN, {"envId": "ENV-VM-03", "reason": "流转维护"})
        record = session.execute(select(MaintenanceRecord).where(
            MaintenanceRecord.reason == "流转维护")).scalar_one()
        maintenance_service.verify(session, ADMIN, record.id, healthy=True)
        flow = status_flow(session, days=7)
        assert flow["days"] == 7
        assert flow["counts"].get("batch_borrow_result", 0) >= 1
        assert flow["counts"].get("maintenance_start", 0) >= 1
        assert flow["counts"].get("maintenance_verify", 0) >= 1
