#!/usr/bin/env python3
"""Claude Code hook -> Agent View forwarder.

Registered for several hook events in ~/.claude/settings.json (see install_hooks.py).
Reads the hook JSON from stdin and POSTs it to the local Agent View server,
starting the server first on SessionStart / UserPromptSubmit if it is not running.

It must never block or break Claude Code: short timeout, all errors swallowed,
always exit 0, and never print to stdout (for UserPromptSubmit / SessionStart,
stdout is injected into the model's context).
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

URL = os.environ.get("AGENT_VIEW_URL", "http://127.0.0.1:7777").rstrip("/") + "/event"
MAX_FIELD = 2000  # tool_response / tool_input can be huge; the dashboard only needs a preview
# If the server is down, these events start it in the background (AGENT_VIEW_AUTOSTART=0 disables).
# UserPromptSubmit covers sessions that were already open when the server went away.
AUTOSTART_EVENTS = {"SessionStart", "UserPromptSubmit"}
SERVER = Path(__file__).resolve().parent / "server.py"
LOG = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "agent-view.log"


def _trim(value):
    s = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return s if len(s) <= MAX_FIELD else s[:MAX_FIELD] + "…"


def main():
    try:
        data = json.loads(sys.stdin.buffer.read() or b"{}")
    except Exception:
        return
    for key in ("tool_response", "last_assistant_message", "prompt", "message"):
        if key in data:
            data[key] = _trim(data[key])
    if "tool_input" in data and len(json.dumps(data["tool_input"], default=str)) > MAX_FIELD * 2:
        data["tool_input"] = {"_truncated": _trim(data["tool_input"])}
    data["_ts"] = time.time()
    body = json.dumps(data, ensure_ascii=False, default=str).encode()
    if post(body) or data.get("hook_event_name") not in AUTOSTART_EVENTS:
        return
    if os.environ.get("AGENT_VIEW_AUTOSTART", "1") != "0" and start_server():
        for _ in range(10):
            time.sleep(0.2)
            if post(body):
                return


def post(body):
    """True if the server accepted the event. Only a refused connection counts as "not running"."""
    try:
        req = urllib.request.Request(URL, data=body, headers={"Content-Type": "application/json"},
                                     method="POST")
        urllib.request.urlopen(req, timeout=0.4).close()
        return True
    except urllib.error.URLError as err:
        return not isinstance(err.reason, ConnectionRefusedError)
    except Exception:
        return True  # slow / odd response: the server is there, don't spawn another one


def start_server():
    """Launch server.py detached from this hook (and from the Claude session) on URL's port."""
    u = urllib.parse.urlsplit(URL)
    if u.hostname not in ("127.0.0.1", "localhost") or not SERVER.exists():
        return False
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "ab") as log:
            subprocess.Popen(
                [sys.executable, str(SERVER), "--host", "127.0.0.1", "--port", str(u.port or 80)],
                stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                start_new_session=True, close_fds=True,
            )
        return True
    except Exception:
        return False


if __name__ == "__main__":
    try:
        main()
    finally:
        sys.exit(0)
