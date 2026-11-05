"""Jenkins Remote API 适配器：受控 Job 提交（DAG 节点编排）+ 动态 Job 编排（发布同步）。

两类能力共用一个客户端：
- DAG 节点编排（services/orchestration.py）：仅操作服务端登记的受控 Job
  （ARGUS_JENKINS_INSTALL_JOB / ARGUS_JENKINS_CASES_JOB），按节点提交与对账。
- 动态流水线（services/provisioning.py + runs.py）：把已发布编排生成为受控前缀
  （ARGUS_JENKINS_JOB_PREFIX，默认 argus-）下的 Job，经 createItem/config.xml 创建/更新，
  运行按 ARGUS_TOKEN 对账 queue→build→终态。

约定：username + API Token 认证（Jenkins 对 API Token 请求豁免 CSRF crumb；无凭据的
一次性本地实例回退 crumb+会话 cookie）。提交绝不重试：结果不明只对账。XML 提交必须
带 charset=utf-8（否则中文被 Jenkins 错误解码）。网络/HTTP 异常统一折算为 JenkinsError，
由调用方转入对账等待。
"""
from __future__ import annotations

import base64
import json
from http.cookiejar import CookieJar
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, build_opener, HTTPRedirectHandler, HTTPCookieProcessor

from ..config import settings
from ..errors import BusinessError


class JenkinsError(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_JOB_NAME_RE = re.compile(r"[A-Za-z0-9_.-]+")


def connection_config() -> dict:
    cfg = settings()
    parsed = urlparse(cfg.jenkins_url)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or
            parsed.password or parsed.query or parsed.fragment):
        raise BusinessError('请先配置服务端 ARGUS_JENKINS_URL。', code='jenkins_unconfigured')
    jobs = {'install': cfg.jenkins_install_job, 'cases': cfg.jenkins_cases_job}
    if any(not re.fullmatch(r'[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*', job) for job in jobs.values()):
        raise BusinessError('服务端受控 Jenkins Job 配置无效。')
    prefix = cfg.jenkins_job_prefix
    if not prefix or not _JOB_NAME_RE.fullmatch(prefix) or not prefix.endswith('-'):
        raise BusinessError('ARGUS_JENKINS_JOB_PREFIX 必须以「-」结尾且仅含字母数字点横线。')
    if cfg.jenkins_kind not in ('mock', 'jenkins'):
        raise BusinessError('ARGUS_JENKINS_KIND 必须为 mock 或 jenkins。')
    return {'url': cfg.jenkins_url.rstrip('/'), 'jobs': jobs, 'kind': cfg.jenkins_kind, 'prefix': prefix}


def job_name_for(prefix: str, definition_id: str) -> str:
    """动态 Job 名：前缀 + 定义 ID 小写（PL-Ab12… → argus-pl-ab12…），确定性可重建。"""
    name = prefix + definition_id.lower()
    if not _JOB_NAME_RE.fullmatch(name) or len(name) > 120:
        raise JenkinsError('动态 Job 名生成失败：定义 ID 含非法字符。')
    return name


class JenkinsClient:
    def __init__(self, config: dict):
        self.config = config
        self.base = config['url']
        self.prefix = config.get('prefix', '')
        self.opener = build_opener(NoRedirect(), HTTPCookieProcessor(CookieJar()))
        self.crumb_headers = {}

    def job_path(self, job: str) -> str:
        if job not in self.config['jobs'].values():
            raise JenkinsError('拒绝未登记的 Jenkins Job')
        return '/' + '/'.join('job/' + quote(part, safe='') for part in job.split('/'))

    def _assert_dynamic_job(self, job: str) -> None:
        if not self.prefix or not job.startswith(self.prefix) or not _JOB_NAME_RE.fullmatch(job):
            raise JenkinsError(f'拒绝操作受控前缀外的 Jenkins Job：{job!r}')

    def dynamic_job_path(self, job: str) -> str:
        self._assert_dynamic_job(job)
        return '/' + '/'.join('job/' + quote(part, safe='') for part in job.split('/'))

    def build_url(self, job: str, build: str) -> str:
        if not str(build).isdigit():
            raise JenkinsError('Jenkins build 标识无效')
        return self.base + self.job_path(job) + '/' + str(build) + '/'

    def build_url_of(self, job: str, build: str) -> str:
        """动态 Job 的构建 URL（同 build_url，但走前缀校验）。"""
        if not str(build).isdigit():
            raise JenkinsError('Jenkins build 标识无效')
        return self.base + self.dynamic_job_path(job) + '/' + str(build) + '/'

    def request(self, path: str, data=None, content_type: str | None = None):
        """data 为 dict（表单编码）或 bytes（配合 content_type，如 XML 提交）。"""
        cfg = settings()
        headers = {'Accept': 'application/json', **self.crumb_headers}
        if cfg.jenkins_username and cfg.jenkins_token:
            token = base64.b64encode(f'{cfg.jenkins_username}:{cfg.jenkins_token}'.encode()).decode()
            headers['Authorization'] = 'Basic ' + token
        if data is not None:
            if isinstance(data, dict):
                body, headers['Content-Type'] = urlencode(data).encode(), 'application/x-www-form-urlencoded'
            else:
                body = data
                headers['Content-Type'] = content_type or 'application/octet-stream'
        else:
            body = None
        req = Request(self.base + path, data=body, headers=headers)
        try:
            with self.opener.open(req, timeout=cfg.jenkins_timeout) as response:
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise JenkinsError('Jenkins 响应超出大小限制')
                return response.status, response.headers, raw
        except HTTPError as exc:
            if exc.code == 404 and data is None:
                return 404, {}, b'{}'
            raise JenkinsError(f'Jenkins HTTP {exc.code}，等待对账') from None
        except (URLError, TimeoutError, OSError):
            raise JenkinsError('Jenkins 网络异常，等待对账') from None

    def get(self, path: str) -> dict:
        status, _, raw = self.request(path)
        if status == 404:
            return {}
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError()
            return data
        except (ValueError, TypeError):
            raise JenkinsError('Jenkins JSON 响应无效') from None

    # ---- DAG 节点编排（受控固定 Job）----

    def submit(self, job: str, params: dict) -> dict:
        # Keep CSRF protection enabled for a disposable local Jenkins without API auth.
        # The cookie jar retains the crumb session; these values never enter snapshots.
        cfg = settings()
        if not (cfg.jenkins_username and cfg.jenkins_token):
            crumb = self.get('/crumbIssuer/api/json')
            if crumb:
                field, value = crumb.get('crumbRequestField', ''), crumb.get('crumb', '')
                if (not isinstance(field, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', field)
                        or field.lower() in ('authorization', 'host', 'cookie', 'content-length')
                        or not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', value)):
                    raise JenkinsError('Jenkins CSRF 响应无效')
                self.crumb_headers[field] = value
        status, headers, _ = self.request(self.job_path(job) + '/buildWithParameters', params)
        location = headers.get('Location', '')
        # Only extract numeric queue ID. Never follow server-provided URLs with credentials.
        parsed = urlparse(location)
        expected = urlparse(self.base)
        path = parsed.path.rstrip('/')
        prefix = expected.path.rstrip('/') + '/queue/item/'
        if status != 201 or (parsed.netloc and (parsed.netloc != expected.netloc or parsed.scheme != expected.scheme)) or not path.startswith(prefix) or not path[len(prefix):].isdigit():
            raise JenkinsError('Jenkins 未返回可确认的 queue ID，等待对账')
        return {'queue': path[len(prefix):]}

    def find(self, job: str, token: str) -> dict | None:
        # A missing token is NOT proof that submission failed. Keep reconciling.
        queue = self.get('/queue/api/json?tree=items[id,actions[parameters[name,value]],task[url]]')
        builds = self.get(self.job_path(job) + '/api/json?tree=builds[number,actions[parameters[name,value]]]')
        def matches(item):
            return any(p.get('name') == 'ARGUS_TOKEN' and p.get('value') == token
                       for a in item.get('actions', []) for p in a.get('parameters', []))
        hits = []
        for kind, collection, key in [('queue', queue.get('items', []), 'id'), ('build', builds.get('builds', []), 'number')]:
            if not isinstance(collection, list):
                raise JenkinsError('Jenkins 队列/构建列表格式无效')
            for item in collection:
                if matches(item):
                    value = item.get(key)
                    if type(value) is not int or value <= 0:
                        raise JenkinsError('Jenkins 队列/构建标识无效')
                    hits.append((kind, str(value)))
        build_hits = {value for kind, value in hits if kind == 'build'}
        queue_hits = {value for kind, value in hits if kind == 'queue'}
        if len(build_hits) > 1 or len(queue_hits) > 1:
            raise JenkinsError('发现重复 ARGUS_TOKEN，需人工核查，不自动释放设备')
        return ({'build': next(iter(build_hits))} if build_hits else
                {'queue': next(iter(queue_hits))} if queue_hits else None)

    def poll(self, job: str, node: dict) -> dict:
        if node.get('build'):
            data = self.get(self.job_path(job) + '/' + node['build'] + '/api/json')
            if not data:
                raise JenkinsError('Jenkins 构建暂不可查，等待对账')
            result = data.get('result')
            if data.get('building') is False and result in ('SUCCESS', 'FAILURE', 'UNSTABLE', 'ABORTED', 'NOT_BUILT'):
                return {'status': 'success' if result == 'SUCCESS' else 'failed', 'result': result,
                        'buildUrl': self.build_url(job, node['build'])}
            return {'status': 'running'}
        if node.get('queue'):
            data = self.get('/queue/item/' + node['queue'] + '/api/json')
            if data.get('cancelled') is True:
                return {'status': 'failed', 'result': 'ABORTED'}
            number = data.get('executable', {}).get('number')
            if type(number) is int and number > 0:
                return {'status': 'running', 'build': str(number), 'buildUrl': self.build_url(job, str(number))}
            if data:
                return {'status': 'queued'}
        match = self.find(job, node['token'])
        if match:
            return {**match, 'status': 'running' if 'build' in match else 'queued'}
        raise JenkinsError('提交结果尚未确认，保留设备占用；不会重复提交')

    # ---- 动态流水线（受控前缀 Job 的创建/更新与运行对账）----

    def get_job_config(self, job: str) -> bytes | None:
        """读取动态 Job 当前 config.xml；不存在返回 None（幂等同步判断）。"""
        status, _, raw = self.request(self.dynamic_job_path(job) + '/config.xml')
        return None if status == 404 else raw

    def create_job(self, job: str, xml: bytes) -> None:
        self._assert_dynamic_job(job)
        status, _, _ = self.request('/createItem?name=' + quote(job, safe=''), xml,
                                    'application/xml; charset=utf-8')
        if status not in (200, 201):
            raise JenkinsError(f'创建 Jenkins Job 失败（HTTP {status}），等待对账。')

    def update_job(self, job: str, xml: bytes) -> None:
        status, _, _ = self.request(self.dynamic_job_path(job) + '/config.xml', xml,
                                    'application/xml; charset=utf-8')
        if status != 200:
            raise JenkinsError(f'更新 Jenkins Job 配置失败（HTTP {status}），等待对账。')

    def submit_dynamic(self, job: str, params: dict) -> dict:
        """提交动态 Job 参数化构建，仅返回从 Location 提取的队列 ID；绝不重试。"""
        status, headers, _ = self.request(self.dynamic_job_path(job) + '/buildWithParameters', params)
        location = headers.get('Location', '')
        parsed, expected = urlparse(location), urlparse(self.base)
        path = parsed.path.rstrip('/')
        prefix = expected.path.rstrip('/') + '/queue/item/'
        if (status != 201
                or (parsed.netloc and (parsed.netloc != expected.netloc or parsed.scheme != expected.scheme))
                or not path.startswith(prefix) or not path[len(prefix):].isdigit()):
            raise JenkinsError('Jenkins 未返回可确认的 queue ID，等待对账。')
        return {'queue': path[len(prefix):]}

    def poll_run(self, job: str, token: str, queue_id: str = '', build: str = '') -> dict:
        """动态运行综合对账：优先已知 build，其次已知 queue，最后按 token 找回。

        返回 {'status': queued|running|success|failed, ...}；结果未知抛 JenkinsError。
        """
        if build:
            data = self.get(self.dynamic_job_path(job) + f'/{build}/api/json')
            if not data:
                raise JenkinsError('Jenkins 构建暂不可查，等待对账。')
            result = data.get('result')
            if data.get('building') is False and result in ('SUCCESS', 'FAILURE', 'UNSTABLE', 'ABORTED', 'NOT_BUILT'):
                return {'status': 'success' if result == 'SUCCESS' else 'failed',
                        'build': build, 'result': result, 'buildUrl': self.build_url_of(job, build)}
            return {'status': 'running', 'build': build}
        if queue_id:
            if not str(queue_id).isdigit():
                raise JenkinsError('Jenkins queue 标识无效。')
            data = self.get(f'/queue/item/{queue_id}/api/json')
            if data.get('cancelled'):
                return {'status': 'failed', 'result': 'ABORTED'}
            number = (data.get('executable') or {}).get('number')
            if type(number) is int and number > 0:
                return {'status': 'running', 'build': str(number), 'buildUrl': self.build_url_of(job, str(number))}
            if data:
                return {'status': 'queued'}
        match = self.find_dynamic(job, token)
        if match:
            return {**match, 'status': 'running' if 'build' in match else 'queued'}
        raise JenkinsError('提交结果尚未确认（队列与近期构建均未见本 token），继续等待对账。')

    def find_dynamic(self, job: str, token: str) -> dict | None:
        """按 ARGUS_TOKEN 在队列与最近构建中对账；找不到不等于失败，调用方保留意图。"""
        queue = self.get('/queue/api/json?tree=items[id,actions[parameters[name,value]],task[url]]')
        builds = self.get(self.dynamic_job_path(job) + '/api/json?tree=builds[number,actions[parameters[name,value]]]')

        def matches(item: dict) -> bool:
            return any(p.get('name') == 'ARGUS_TOKEN' and p.get('value') == token
                       for a in item.get('actions', []) for p in a.get('parameters', []))

        build_hits = {str(i['number']) for i in builds.get('builds', []) if matches(i)}
        queue_hits = {str(i['id']) for i in queue.get('items', []) if matches(i)}
        if len(build_hits) > 1 or len(queue_hits) > 1:
            raise JenkinsError('发现重复 ARGUS_TOKEN，需人工核查，不自动释放资源。')
        if build_hits:
            return {'build': next(iter(build_hits))}
        if queue_hits:
            return {'queue': next(iter(queue_hits))}
        return None
