"""Snapshot a dormant Agent Server; credentials arrive through /api/init."""

import json
import os
import subprocess
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

env = {
    **os.environ,
    'OH_DEFERRED_INIT': '1',
    'OH_SECRET_KEY': os.environ['LAMBDA_MICROVM_INIT_API_KEY'],
}
process = subprocess.Popen(
    ['openhands-agent-server', '--host', '0.0.0.0', '--port', '8000'], env=env
)


class Hooks(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == '/aws/lambda-microvms/runtime/v1/ready':
            try:
                with urllib.request.urlopen(
                    'http://127.0.0.1:8000/api/init', timeout=2
                ) as response:
                    ready = json.load(response).get('state') == 'dormant'
            except Exception:
                ready = False
            self.send_response(200 if ready else 503)
        elif self.path in [
            '/aws/lambda-microvms/runtime/v1/' + name
            for name in ('run', 'resume', 'suspend', 'terminate', 'validate')
        ]:
            self.send_response(200)
        else:
            self.send_response(404)
        self.end_headers()

    def log_message(self, *args):
        pass


threading.Thread(
    target=ThreadingHTTPServer(('0.0.0.0', 8080), Hooks).serve_forever, daemon=True
).start()
raise SystemExit(process.wait())
