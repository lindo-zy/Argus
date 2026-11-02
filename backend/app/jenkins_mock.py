"""Standalone Jenkins Remote API mock; persists queue/builds in a local SQLite file.

This performs no device/script execution. It serves Jenkins-compatible submission,
queue lookup, token reconciliation, build result and console references over HTTP.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import sqlite3
import time
from urllib.parse import parse_qs, urlparse

PARAMETERS = {'ARGUS_TOKEN', 'RUN_ID', 'NODE_ID', 'ENV_ID', 'IMAGE_REF', 'DEVICE_IDS', 'CASE_REFS', 'PIPELINE_HASH'}
JOBS = {'argus-install', 'argus-cases'}


def make_server(host='127.0.0.1', port=18080, state='/tmp/argus-jenkins-mock.sqlite',
                queue_delay=1.0, build_delay=2.0, fail_nodes=(), drop_nodes=()):
    db = sqlite3.connect(state)
    db.execute('CREATE TABLE IF NOT EXISTS builds (id INTEGER PRIMARY KEY AUTOINCREMENT, job TEXT NOT NULL, params TEXT NOT NULL, created REAL NOT NULL)')
    db.commit(); db.close()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # request bodies and authentication are never logged

        def reply(self, status, value, headers=None, content_type='application/json'):
            raw = json.dumps(value).encode() if content_type == 'application/json' else value.encode()
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(raw)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers(); self.wfile.write(raw)

        def rows(self):
            with sqlite3.connect(state) as db:
                return [{'id': row[0], 'job': row[1], 'params': json.loads(row[2]), 'created': row[3]}
                        for row in db.execute('SELECT id,job,params,created FROM builds ORDER BY id DESC')]

        def actions(self, row):
            return [{'parameters': [{'name': k, 'value': v} for k, v in row['params'].items()]}]

        def do_POST(self):
            path = urlparse(self.path).path.strip('/').split('/')
            if len(path) != 3 or path[0] != 'job' or path[1] not in JOBS or path[2] != 'buildWithParameters':
                return self.reply(404, {'error': 'unknown controlled job'})
            size = int(self.headers.get('Content-Length', '0'))
            if size > 65536:
                return self.reply(413, {})
            values = parse_qs(self.rfile.read(size).decode(), keep_blank_values=True)
            params = {k: v[0] for k, v in values.items() if len(v) == 1}
            if set(params) != PARAMETERS or not all(params.values()):
                return self.reply(400, {'error': 'invalid parameters'})
            try:
                assert isinstance(json.loads(params['DEVICE_IDS']), list)
                assert isinstance(json.loads(params['CASE_REFS']), list)
            except (ValueError, AssertionError):
                return self.reply(400, {'error': 'invalid snapshots'})
            with sqlite3.connect(state) as db:
                cur = db.execute('INSERT INTO builds(job,params,created) VALUES (?,?,?)',
                                 (path[1], json.dumps(params), time.time()))
                queue_id = cur.lastrowid
            if params['NODE_ID'] in drop_nodes:
                self.close_connection = True
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                return
            return self.reply(201, {}, {'Location': f'/queue/item/{queue_id}/'})

        def do_GET(self):
            path = urlparse(self.path).path.strip('/').split('/')
            rows = self.rows()
            if path == ['healthz']:
                return self.reply(200, {'kind': 'jenkins-mock', 'submissions': len(rows)})
            if path == ['queue', 'api', 'json']:
                return self.reply(200, {'items': [{'id': r['id'], 'actions': self.actions(r)} for r in rows
                    if time.time() - r['created'] < queue_delay]})
            if len(path) == 5 and path[:2] == ['queue', 'item'] and path[3:] == ['api', 'json']:
                row = next((r for r in rows if str(r['id']) == path[2]), None)
                if not row:
                    return self.reply(404, {})
                return self.reply(200, {'id': row['id'], 'cancelled': False, 'actions': self.actions(row),
                    **({'executable': {'number': row['id']}} if time.time() - row['created'] >= queue_delay else {})})
            if len(path) >= 3 and path[0] == 'job' and path[1] in JOBS:
                if path[2:] == ['api', 'json']:
                    return self.reply(200, {'builds': [{'number': r['id'], 'actions': self.actions(r)}
                        for r in rows if r['job'] == path[1] and time.time() - r['created'] >= queue_delay]})
                row = next((r for r in rows if str(r['id']) == path[2] and r['job'] == path[1]), None)
                if not row:
                    return self.reply(404, {})
                done = time.time() - row['created'] >= queue_delay + build_delay
                if len(path) == 3:
                    return self.reply(200, {'kind': 'jenkins-mock', 'job': row['job'], 'number': row['id'],
                        'node': row['params']['NODE_ID'], 'run': row['params']['RUN_ID'],
                        'state': 'completed' if done else 'running', 'deviceExecution': False})
                if path[3:] == ['api', 'json']:
                    return self.reply(200, {'number': row['id'], 'building': not done,
                        'result': ('FAILURE' if row['params']['NODE_ID'] in fail_nodes else 'SUCCESS') if done else None,
                        'actions': self.actions(row)})
                if path[3:] == ['consoleText']:
                    return self.reply(200, f"[jenkins-mock] job={row['job']} node={row['params']['NODE_ID']}\n"
                        f"No device or test execution. state={'completed' if done else 'running'}\n", content_type='text/plain')
            self.reply(404, {})

    return ThreadingHTTPServer((host, port), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=18080)
    parser.add_argument('--state', default='/tmp/argus-jenkins-mock.sqlite')
    parser.add_argument('--queue-delay', type=float, default=1)
    parser.add_argument('--build-delay', type=float, default=2)
    parser.add_argument('--fail-node', action='append', default=[])
    parser.add_argument('--drop-response-node', action='append', default=[])
    args = parser.parse_args()
    make_server(args.host, args.port, str(Path(args.state).absolute()), args.queue_delay,
                args.build_delay, args.fail_node, args.drop_response_node).serve_forever()


if __name__ == '__main__':
    main()
