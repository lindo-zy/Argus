"""Small Jenkins Remote API adapter. URLs/jobs come exclusively from server config.

Use username + API token (Jenkins exempts API-token requests from CSRF crumbs).
Never retry a submission: even an HTTP error can follow acceptance by a proxy.
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


def connection_config() -> dict:
    cfg = settings()
    parsed = urlparse(cfg.jenkins_url)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or
            parsed.password or parsed.query or parsed.fragment):
        raise BusinessError('请先配置服务端 ARGUS_JENKINS_URL。', code='jenkins_unconfigured')
    jobs = {'install': cfg.jenkins_install_job, 'cases': cfg.jenkins_cases_job}
    if any(not re.fullmatch(r'[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*', job) for job in jobs.values()):
        raise BusinessError('服务端受控 Jenkins Job 配置无效。')
    if cfg.jenkins_kind not in ('mock', 'jenkins'):
        raise BusinessError('ARGUS_JENKINS_KIND 必须为 mock 或 jenkins。')
    return {'url': cfg.jenkins_url.rstrip('/'), 'jobs': jobs, 'kind': cfg.jenkins_kind}


class JenkinsClient:
    def __init__(self, config: dict):
        self.config = config
        self.base = config['url']
        self.opener = build_opener(NoRedirect(), HTTPCookieProcessor(CookieJar()))
        self.crumb_headers = {}

    def job_path(self, job: str) -> str:
        if job not in self.config['jobs'].values():
            raise JenkinsError('拒绝未登记的 Jenkins Job')
        return '/' + '/'.join('job/' + quote(part, safe='') for part in job.split('/'))

    def build_url(self, job: str, build: str) -> str:
        if not str(build).isdigit():
            raise JenkinsError('Jenkins build 标识无效')
        return self.base + self.job_path(job) + '/' + str(build) + '/'

    def request(self, path: str, data: dict | None = None):
        cfg = settings()
        headers = {'Accept': 'application/json', **self.crumb_headers}
        if cfg.jenkins_username and cfg.jenkins_token:
            token = base64.b64encode(f'{cfg.jenkins_username}:{cfg.jenkins_token}'.encode()).decode()
            headers['Authorization'] = 'Basic ' + token
        body = urlencode(data).encode() if data is not None else None
        if body is not None:
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
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
