"""Jenkins Remote API 客户端：受控前缀 Job 的动态创建/更新、构建提交与对账。

约定：
- 仅操作受控前缀（ARGUS_JENKINS_JOB_PREFIX，默认 argus-）下的 Job，其余一律拒绝，
  与 AGENTS.md「流水线配置只允许受控 Job」一致；配置均由 Argus 生成，不导入外部 XML。
- 使用 username + API Token 的 Basic 认证（Jenkins 对 API Token 请求豁免 CSRF crumb）。
- 构建提交绝不重试：提交结果不明时按 ARGUS_TOKEN 对账找回，避免重复触发。
- 网络/HTTP 异常统一折算为 JenkinsError，由调用方转入 reconciling 等待后台对账。
"""
from __future__ import annotations

import base64
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, build_opener
from urllib.request import HTTPRedirectHandler

from ..config import settings
from ..errors import BusinessError


class JenkinsError(Exception):
    """Jenkins 调用失败：结果未知，等待对账，不得据此重复提交。"""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_JOB_NAME_RE = re.compile(r"[A-Za-z0-9_.-]+")


def connection_config() -> dict:
    """校验并汇总连接配置；未配置 URL 时返回 None（演示模拟模式）。"""
    cfg = settings()
    if cfg.jenkins_kind not in ("mock", "jenkins"):
        raise BusinessError("ARGUS_JENKINS_KIND 只能为 mock 或 jenkins。")
    if cfg.jenkins_kind == "mock":
        return None
    parsed = urlparse(cfg.jenkins_url)
    if (parsed.scheme not in ("http", "https") or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise BusinessError("请配置服务端 ARGUS_JENKINS_URL（形如 http://127.0.0.1:18080）。", code="jenkins_unconfigured")
    prefix = cfg.jenkins_job_prefix
    if not prefix or not _JOB_NAME_RE.fullmatch(prefix) or not prefix.endswith("-"):
        raise BusinessError("ARGUS_JENKINS_JOB_PREFIX 必须以「-」结尾且仅含字母数字点横线。")
    if not (cfg.jenkins_username and cfg.jenkins_token):
        raise BusinessError("请配置 ARGUS_JENKINS_USERNAME / ARGUS_JENKINS_TOKEN。", code="jenkins_unconfigured")
    return {"url": cfg.jenkins_url.rstrip("/"), "prefix": prefix}


def job_name_for(prefix: str, definition_id: str) -> str:
    """受控 Job 名：前缀 + 定义 ID 小写（PL-Ab12… → argus-pl-ab12…），确定性且可重建。"""
    name = prefix + definition_id.lower()
    if not _JOB_NAME_RE.fullmatch(name) or len(name) > 120:
        raise JenkinsError("受控 Job 名生成失败：定义 ID 含非法字符。")
    return name


class JenkinsClient:
    def __init__(self, config: dict):
        self.config = config
        self.base = config["url"]
        self.prefix = config["prefix"]
        self.opener = build_opener(_NoRedirect())

    # ---- 路径与安全 ----

    def _assert_controlled(self, job: str) -> None:
        if not job.startswith(self.prefix) or not _JOB_NAME_RE.fullmatch(job):
            raise JenkinsError(f"拒绝操作受控前缀外的 Jenkins Job：{job!r}")

    def job_path(self, job: str) -> str:
        self._assert_controlled(job)
        return "/" + "/".join("job/" + quote(part, safe="") for part in job.split("/"))

    def build_url(self, job: str, number: str) -> str:
        if not str(number).isdigit():
            raise JenkinsError("Jenkins build 标识无效。")
        return f"{self.base}{self.job_path(job)}/{number}/"

    # ---- HTTP ----

    def request(self, path: str, data: bytes | None = None,
                content_type: str = "application/x-www-form-urlencoded") -> tuple[int, dict, bytes]:
        cfg = settings()
        headers = {"Accept": "application/json"}
        if cfg.jenkins_username and cfg.jenkins_token:
            raw = f"{cfg.jenkins_username}:{cfg.jenkins_token}".encode()
            headers["Authorization"] = "Basic " + base64.b64encode(raw).decode()
        if data is not None:
            headers["Content-Type"] = content_type
        req = Request(self.base + path, data=data, headers=headers)
        try:
            with self.opener.open(req, timeout=cfg.jenkins_timeout) as response:
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise JenkinsError("Jenkins 响应超出大小限制。")
                return response.status, dict(response.headers), raw
        except HTTPError as exc:
            if exc.code in (404, 400) and data is None:
                return exc.code, {}, b"{}"
            raise JenkinsError(f"Jenkins HTTP {exc.code}，等待对账。") from None
        except (URLError, TimeoutError, OSError):
            raise JenkinsError("Jenkins 网络异常，等待对账。") from None

    def get_json(self, path: str) -> dict:
        status, _, raw = self.request(path)
        if status == 404:
            return {}
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("not a dict")
            return data
        except ValueError:
            raise JenkinsError("Jenkins JSON 响应无效。") from None

    # ---- 动态编排：Job 配置的创建 / 更新 / 读取 ----

    def get_job_config(self, job: str) -> bytes | None:
        """读取 Job 当前 config.xml；不存在返回 None（用于幂等同步判断）。"""
        status, _, raw = self.request(self.job_path(job) + "/config.xml")
        return None if status == 404 else raw

    def create_job(self, job: str, xml: bytes) -> None:
        self._assert_controlled(job)
        status, _, _ = self.request(
            "/createItem?name=" + quote(job, safe=""), data=xml,
            content_type="application/xml; charset=utf-8")
        if status not in (200, 201):
            raise JenkinsError(f"创建 Jenkins Job 失败（HTTP {status}），等待对账。")

    def update_job(self, job: str, xml: bytes) -> None:
        status, _, _ = self.request(
            self.job_path(job) + "/config.xml", data=xml,
            content_type="application/xml; charset=utf-8")
        if status != 200:
            raise JenkinsError(f"更新 Jenkins Job 配置失败（HTTP {status}），等待对账。")

    def job_exists(self, job: str) -> bool:
        return bool(self.get_json(self.job_path(job) + "/api/json?tree=name"))

    # ---- 调度：构建提交与状态对账 ----

    def submit(self, job: str, params: dict) -> dict:
        """提交参数化构建，仅返回从 Location 提取的队列 ID；绝不重试。"""
        status, headers, _ = self.request(
            self.job_path(job) + "/buildWithParameters",
            data=urlencode({k: str(v) for k, v in params.items()}).encode())
        location = headers.get("Location", "")
        parsed, expected = urlparse(location), urlparse(self.base)
        path = parsed.path.rstrip("/")
        prefix = expected.path.rstrip("/") + "/queue/item/"
        if (status != 201
                or (parsed.netloc and (parsed.netloc != expected.netloc or parsed.scheme != expected.scheme))
                or not path.startswith(prefix) or not path[len(prefix):].isdigit()):
            raise JenkinsError("Jenkins 未返回可确认的 queue ID，等待对账。")
        return {"queue": path[len(prefix):]}

    def queue_item(self, queue_id: str) -> dict:
        if not str(queue_id).isdigit():
            raise JenkinsError("Jenkins queue 标识无效。")
        return self.get_json(f"/queue/item/{queue_id}/api/json")

    def build_info(self, job: str, number: str) -> dict:
        if not str(number).isdigit():
            raise JenkinsError("Jenkins build 标识无效。")
        data = self.get_json(self.job_path(job) + f"/{number}/api/json")
        if not data:
            raise JenkinsError("Jenkins 构建暂不可查，等待对账。")
        return data

    def find(self, job: str, token: str) -> dict | None:
        """按 ARGUS_TOKEN 在队列与最近构建中对账；找不到不等于失败，调用方继续保留意图。"""
        queue = self.get_json("/queue/api/json?tree=items[id,actions[parameters[name,value]],task[url]]")
        builds = self.get_json(
            self.job_path(job) + "/api/json?tree=builds[number,actions[parameters[name,value]]]{0,30}")

        def matches(item: dict) -> bool:
            return any(p.get("name") == "ARGUS_TOKEN" and p.get("value") == token
                       for a in item.get("actions", []) for p in a.get("parameters", []))

        build_hits = {str(i["number"]) for i in builds.get("builds", []) if matches(i)}
        queue_hits = {str(i["id"]) for i in queue.get("items", []) if matches(i)}
        if len(build_hits) > 1 or len(queue_hits) > 1:
            raise JenkinsError("发现重复 ARGUS_TOKEN，需人工核查，不自动释放资源。")
        if build_hits:
            return {"build": build_hits.pop()}
        if queue_hits:
            return {"queue": queue_hits.pop()}
        return None

    def poll(self, job: str, token: str, queue_id: str = "", build: str = "") -> dict:
        """综合对账：优先已知 build，其次已知 queue，最后按 token 找回。

        返回 {'status': queued|running|success|failed, ...}；结果未知抛 JenkinsError。
        """
        if build:
            data = self.build_info(job, build)
            result = data.get("result")
            if not data.get("building", True) and result in ("SUCCESS", "FAILURE", "UNSTABLE", "ABORTED", "NOT_BUILT"):
                return {"status": "success" if result == "SUCCESS" else "failed",
                        "build": build, "result": result, "buildUrl": self.build_url(job, build)}
            return {"status": "running", "build": build}
        if queue_id:
            data = self.queue_item(queue_id)
            if data.get("cancelled"):
                return {"status": "failed", "result": "ABORTED"}
            number = (data.get("executable") or {}).get("number")
            if type(number) is int and number > 0:
                return {"status": "running", "build": str(number), "buildUrl": self.build_url(job, str(number))}
            if data:
                return {"status": "queued"}
        match = self.find(job, token)
        if match:
            return {**match, "status": "running" if "build" in match else "queued"}
        raise JenkinsError("提交结果尚未确认（队列与近期构建均未见本 token），继续等待对账。")
