"""应用配置：数据库、演示身份与 Jenkins 编排接入。"""
from __future__ import annotations

import os
from functools import lru_cache


class Settings:
    """环境变量优先，缺省指向 docker-compose 的开发库。"""

    def __init__(self) -> None:
        self.database_url: str = os.environ.get(
            "ARGUS_DATABASE_URL",
            "postgresql+psycopg://argus:argus@127.0.0.1:5433/argus",
        )
        # Jenkins 动态流水线：未配置 URL 时退回演示模拟；显式 ARGUS_JENKINS_KIND 优先。
        self.jenkins_url: str = os.environ.get("ARGUS_JENKINS_URL", "").strip()
        self.jenkins_kind: str = os.environ.get("ARGUS_JENKINS_KIND", "").strip() or (
            "jenkins" if self.jenkins_url else "mock")
        self.jenkins_username: str = os.environ.get("ARGUS_JENKINS_USERNAME", "argus")
        self.jenkins_token: str = os.environ.get("ARGUS_JENKINS_TOKEN", "")
        self.jenkins_timeout: float = float(os.environ.get("ARGUS_JENKINS_TIMEOUT", "5"))
        # 受控 Job 前缀：Argus 只创建/更新该前缀下的 Job，其余一律拒绝。
        self.jenkins_job_prefix: str = os.environ.get("ARGUS_JENKINS_JOB_PREFIX", "argus-")
        self.jenkins_worker_enabled: bool = os.environ.get("ARGUS_JENKINS_WORKER", "1") == "1"
        self.jenkins_poll_interval: float = float(os.environ.get("ARGUS_JENKINS_POLL_INTERVAL", "5"))
        self.default_workspace_id: str = "WS-DEFAULT"
        # 演示身份：真实身份源接入前的过渡方案（docs/05 待调研项）。
        self.bootstrap_admin_token: str = os.environ.get("ARGUS_ADMIN_TOKEN", "argus-demo-admin")
        self.bootstrap_member_token: str = os.environ.get("ARGUS_MEMBER_TOKEN", "argus-demo-member")
        self.seed_on_empty: bool = os.environ.get("ARGUS_SEED_ON_EMPTY", "1") == "1"


@lru_cache
def settings() -> Settings:
    return Settings()
