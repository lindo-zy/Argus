"""应用配置：数据库与演示身份。"""
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
        self.jenkins_url = os.environ.get("ARGUS_JENKINS_URL", "")
        self.jenkins_kind = os.environ.get("ARGUS_JENKINS_KIND", "jenkins")
        self.jenkins_install_job = os.environ.get("ARGUS_JENKINS_INSTALL_JOB", "argus-install")
        self.jenkins_cases_job = os.environ.get("ARGUS_JENKINS_CASES_JOB", "argus-cases")
        self.jenkins_username = os.environ.get("ARGUS_JENKINS_USERNAME", "")
        self.jenkins_token = os.environ.get("ARGUS_JENKINS_TOKEN", "")
        self.jenkins_timeout = 5
        self.jenkins_worker_enabled = os.environ.get("ARGUS_JENKINS_WORKER", "1") == "1"
        self.default_workspace_id: str = "WS-DEFAULT"
        # 演示身份：真实身份源接入前的过渡方案（docs/05 待调研项）。
        self.bootstrap_admin_token: str = os.environ.get("ARGUS_ADMIN_TOKEN", "argus-demo-admin")
        self.bootstrap_member_token: str = os.environ.get("ARGUS_MEMBER_TOKEN", "argus-demo-member")
        self.seed_on_empty: bool = os.environ.get("ARGUS_SEED_ON_EMPTY", "1") == "1"


@lru_cache
def settings() -> Settings:
    return Settings()
