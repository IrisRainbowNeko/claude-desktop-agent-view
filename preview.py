#!/usr/bin/env python3
"""Preview entry point for the Claude Desktop Browser pane.

The real server has to stay on the hook port (7777), and it may already be running,
started by hook.py or systemd. The Browser pane refuses to preview a port it did not
start, so this listens on the port the pane assigns ($PORT) and proxies everything,
SSE included, to the real server, starting it first if nothing is listening there.
"""
import http.client
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hook  # noqa: E402  (reuses its URL and detached start_server)

UPSTREAM = hook.urllib.parse.urlsplit(hook.URL)
UP_HOST, UP_PORT = UPSTREAM.hostname, UPSTREAM.port or 80
HOP = {"connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade",
       "proxy-authorization", "proxy-authenticate", "content-length"}


def upstream_up():
    try:
        c = http.client.HTTPConnection(UP_HOST, UP_PORT, timeout=2)
        c.request("GET", "/api/hooklog")
        ok = c.getresponse().status == 200
        c.close()
        return ok
    except OSError:
        return False


def ensure_upstream():
    if upstream_up():
        return
    print(f"agent-view preview: starting server on {UP_HOST}:{UP_PORT}", flush=True)
    hook.start_server()
    for _ in range(25):
        time.sleep(0.2)
        if upstream_up():
            return
    sys.exit(f"agent-view preview: server on {UP_HOST}:{UP_PORT} did not come up, see {hook.LOG}")


class Proxy(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def forward(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)) or None
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP and k.lower() != "host"}
        try:
            c = http.client.HTTPConnection(UP_HOST, UP_PORT, timeout=60)
            c.request(self.command, self.path, body=body, headers=headers)
            r = c.getresponse()
        except OSError:
            ensure_upstream()  # server went away (e.g. killed); bring it back for the next request
            self.send_error(502, "Agent View server unavailable, retry")
            return
        self.send_response(r.status)
        for k, v in r.getheaders():
            if k.lower() not in HOP:
                self.send_header(k, v)
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        try:
            while chunk := r.read1(65536):  # read1: pass SSE events through as they arrive
                self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
        except OSError:
            self.close_connection = True
        finally:
            c.close()

    do_GET = do_POST = forward

    def log_message(self, *args):
        pass


def main():
    port = int(os.environ.get("PORT") or (sys.argv[1] if len(sys.argv) > 1 else 7778))
    ensure_upstream()
    print(f"agent-view preview: http://127.0.0.1:{port} -> {UP_HOST}:{UP_PORT}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Proxy).serve_forever()


if __name__ == "__main__":
    main()
