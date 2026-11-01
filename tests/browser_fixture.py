"""Isolated synthetic sessions for browser tests; never reads the user's history."""
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
from session_catalog import project_identity


ROWS = []
NOW = time.time()
for i in range(1000):
    cwd = f"D:/projects/project-{i // 25:03}"
    ROWS.append({"id": f"session-{i:04}", "project": "encoded", "project_id": project_identity(cwd, None),
                 "cwd": cwd, "host": None, "title": f"测试会话 {i}", "mtime": NOW - i,
                 "status": "running" if i == 0 else "idle", "running_subagents": 70 if i == 0 else 0,
                 "subagents": 258 if i == 0 else 0, "entrypoint": "browser-fixture", "started": NOW - 30})


def node(aid, parent, status="done", label=None):
    return {"id": aid, "parent": parent, "status": status, "label": label or aid,
            "started": NOW - 30, "last": NOW - 1, "mtime": NOW - 1, "tool_calls": 1, "out_tokens": 10,
            "context_tokens": 20, "errors": 0, "top_tools": [], "tool": None,
            "agent_type": "worker", "background": False, "model": "test"}


NODES = [node("main", None, "running", "大型测试会话")]
for i in range(250):
    NODES.append(node(f"agent-{i:03}", "main", "running" if i < 60 else "waiting" if i < 65 else "stale" if i < 70 else "done"))
parent = "agent-230"
for depth in range(8):
    aid = f"deep-{depth}"
    NODES.append(node(aid, parent, "running" if depth == 7 else "done", "稀有的深层任务" if depth == 7 else aid))
    parent = aid

server.CATALOG = SimpleNamespace(snapshot=lambda: {"rows": ROWS, "indexing": False,
    "indexed": len(ROWS), "expected": len(ROWS), "index_error": None})
server.FOCUS = SimpleNamespace(current=None)
server.api_session = lambda q: {"session": next((r for r in ROWS if r["id"] == q.get("id", [""])[0]), ROWS[0]),
                                "nodes": NODES, "hooks_last": 0, "now": NOW}
server.api_transcript = lambda q: {"items": [{"role": "assistant", "text": "记录：" + q.get("agent", ["main"])[0], "ts": 1}],
                                  "truncated": False, "size": 10}


class FixtureHandler(server.Handler):
    def do_POST(self):
        if self.path == "/__test__/update":
            NODES[5]["label"] = "任务状态已更新"
            NODES[5]["out_tokens"] += 1
            NODES[5]["errors"] += 1
            NODES.reverse()  # source ordering can change; existing siblings must not move
            server.broadcast({"type": "changed", "sessions": ["session-0000"]})
            return self._json({"ok": True})
        return self._json({"error": "fixture only"}, 404)


if __name__ == "__main__":
    httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
    httpd.daemon_threads = True
    print(json.dumps({"port": httpd.server_port}), flush=True)
    httpd.serve_forever()
