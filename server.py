#!/usr/bin/env python3
"""Agent View: a local dashboard for Claude Code sessions and their subagents.

Stdlib only. Two data sources are merged:

* Transcripts under ~/.claude/projects/<project>/<session>.jsonl and
  <session>/subagents/agent-<id>.jsonl (+ .meta.json). Parsed incrementally and
  polled for changes, so the dashboard works even without hooks.
* Hook events POSTed by hook.py to /event. These add precise live state
  (waiting for permission, turn finished, subagent stopped).

Endpoints:
  GET  /                    dashboard (static/index.html)
  GET  /events              Server-Sent Events stream ("changed" / "hook")
  GET  /api/sessions        recent sessions
  GET  /api/session?id=     agent tree of one session
  GET  /api/transcript?session=&agent=[&full=1]
  POST /event               hook payload from hook.py
"""
import argparse
import json
import os
import queue
import sys
import threading
import time
import urllib.request
from collections import Counter, deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
CLAUDE_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
PROJECTS = CLAUDE_DIR / "projects"

AGENT_TOOLS = {"Agent", "Task"}
WAITING_TOOLS = {"AskUserQuestion", "ExitPlanMode"}
BRIEF_KEYS = ("command", "file_path", "notebook_path", "path", "pattern", "url", "query",
              "description", "skill", "prompt")
RUNNING_GRACE = 300      # s without writes before a mid-turn transcript counts as stale
TOOL_GRACE = 1800        # s a tool call may stay open (long Bash etc.)
TAIL_BYTES = 3 * 1024 * 1024
TAIL_ITEMS = 400


def parse_ts(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return None


def tool_brief(inp):
    if not isinstance(inp, dict):
        return ""
    for k in BRIEF_KEYS:
        v = inp.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip().splitlines()[0][:200]
    for v in inp.values():
        if isinstance(v, str) and v.strip():
            return v.strip().splitlines()[0][:200]
    return ""


def flatten(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict):
                if b.get("type") == "text":
                    parts.append(b.get("text", ""))
                elif b.get("type") == "image":
                    parts.append("[image]")
                else:
                    parts.append(json.dumps(b, ensure_ascii=False)[:500])
            else:
                parts.append(str(b))
        return "\n".join(parts)
    return "" if content is None else json.dumps(content, ensure_ascii=False)


# --------------------------------------------------------------------------- transcripts

class Transcript:
    """Incrementally parsed summary of one .jsonl transcript."""

    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        self.offset = 0
        self.mtime = 0.0
        self.title = self.custom_title = self.last_prompt = None
        self.cwd = self.model = self.entrypoint = None
        self.first_ts = self.last_ts = None
        self.out_tokens = 0
        self.context_tokens = 0
        self.tool_calls = 0
        self.errors = 0
        self.tools = Counter()
        self.open_tools = {}    # tool_use_id -> {name, brief, ts}
        self.agent_calls = {}   # tool_use_id -> {description, subagent_type, status, agent_id}
        self.last_kind = None   # user | assistant_tool | assistant_partial | assistant_end
        self._msg_id = None
        self._msg_out = 0

    def refresh(self):
        with self.lock:
            try:
                st = self.path.stat()
            except FileNotFoundError:
                return False
            if st.st_size < self.offset:
                self.reset()
            if st.st_size == self.offset:
                self.mtime = st.st_mtime
                return False
            with open(self.path, "rb") as f:
                f.seek(self.offset)
                chunk = f.read()
            end = chunk.rfind(b"\n")
            self.mtime = st.st_mtime
            if end < 0:
                return False
            for line in chunk[:end].split(b"\n"):
                if line.strip():
                    try:
                        self._feed(json.loads(line))
                    except (ValueError, AttributeError, TypeError):
                        pass
            self.offset += end + 1
            return True

    def _feed(self, d):
        t = d.get("type")
        ts = parse_ts(d.get("timestamp"))
        if ts:
            self.first_ts = self.first_ts or ts
            self.last_ts = ts
        if t == "ai-title":
            self.title = d.get("aiTitle") or self.title
        elif t == "custom-title":
            self.custom_title = d.get("customTitle") or self.custom_title
        elif t == "last-prompt":
            self.last_prompt = d.get("lastPrompt") or self.last_prompt
        if d.get("cwd"):
            self.cwd = d["cwd"]
        if d.get("entrypoint"):
            self.entrypoint = d["entrypoint"]
        msg = d.get("message")
        if not isinstance(msg, dict):
            return
        content = msg.get("content")
        if t == "assistant":
            if msg.get("model") and msg["model"] != "<synthetic>":
                self.model = msg["model"]
            usage = msg.get("usage") or {}
            out = usage.get("output_tokens") or 0
            # one API message may be split into several entries that repeat the usage
            if msg.get("id") and msg.get("id") == self._msg_id:
                self.out_tokens -= self._msg_out
            self._msg_id, self._msg_out = msg.get("id"), out
            self.out_tokens += out
            ctx = sum(usage.get(k) or 0 for k in
                      ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
            if ctx:
                self.context_tokens = ctx
            has_tool = False
            for b in content if isinstance(content, list) else []:
                if not isinstance(b, dict) or b.get("type") != "tool_use":
                    continue
                has_tool = True
                name, inp = b.get("name"), b.get("input") or {}
                self.tool_calls += 1
                self.tools[name] += 1
                self.open_tools[b.get("id")] = {"name": name, "brief": tool_brief(inp), "ts": ts}
                if name in AGENT_TOOLS:
                    self.agent_calls[b.get("id")] = {
                        "description": inp.get("description"),
                        "subagent_type": inp.get("subagent_type"),
                        "status": "pending", "agent_id": None,
                    }
            stop = msg.get("stop_reason")
            if has_tool or stop == "tool_use":
                self.last_kind = "assistant_tool"
            elif stop:
                self.last_kind = "assistant_end"
            else:
                self.last_kind = "assistant_partial"
        elif t == "user":
            for b in content if isinstance(content, list) else []:
                if not isinstance(b, dict) or b.get("type") != "tool_result":
                    continue
                tid = b.get("tool_use_id")
                self.open_tools.pop(tid, None)
                if b.get("is_error"):
                    self.errors += 1
                if tid in self.agent_calls:
                    r = d.get("toolUseResult")
                    if isinstance(r, dict):
                        self.agent_calls[tid]["status"] = r.get("status") or "completed"
                        self.agent_calls[tid]["agent_id"] = r.get("agentId")
            if not d.get("isMeta"):
                self.last_kind = "user"
                if isinstance(content, str) or any(
                        isinstance(b, dict) and b.get("type") == "text" for b in content or []):
                    # a new human turn: anything still open from the previous one was interrupted
                    self.open_tools.clear()

    def jsonl_status(self, is_sub, now):
        age = now - self.mtime
        open_now = [o for o in self.open_tools.values() if now - (o["ts"] or 0) < TOOL_GRACE]
        if open_now:
            if any(o["name"] in WAITING_TOOLS for o in open_now):
                return "waiting"
            return "running"
        if self.last_kind in ("user", "assistant_tool", "assistant_partial"):
            return "running" if age < RUNNING_GRACE else "stale"
        if self.last_kind == "assistant_end":
            return "done" if is_sub else "idle"
        return "idle"

    def current_tool(self):
        if not self.open_tools:
            return None
        return max(self.open_tools.values(), key=lambda o: o["ts"] or 0)


_transcripts = {}
_transcripts_lock = threading.Lock()


def get_transcript(path):
    with _transcripts_lock:
        tr = _transcripts.get(path)
        if tr is None:
            tr = _transcripts[path] = Transcript(path)
    tr.refresh()
    return tr


def main_session_files():
    try:
        files = [(p.stat().st_mtime, p) for p in PROJECTS.glob("*/*.jsonl")]
    except OSError:
        return []
    files.sort(key=lambda x: x[0], reverse=True)
    return files


def find_session(sid):
    if not sid or "/" in sid or ".." in sid:
        return None
    hits = list(PROJECTS.glob(f"*/{sid}.jsonl"))
    return hits[0] if hits else None


def subagent_files(session_path):
    d = session_path.with_suffix("") / "subagents"
    return sorted(d.glob("agent-*.jsonl")) if d.is_dir() else []


def read_meta(agent_path):
    try:
        return json.loads(agent_path.with_suffix(".meta.json").read_text())
    except (OSError, ValueError):
        return {}


# --------------------------------------------------------------------------- live hook state

class LiveState:
    def __init__(self):
        self.lock = threading.Lock()
        self.sessions = {}          # session_id -> {agent_id|"main": {status, tool, ts}}
        self.log = deque(maxlen=300)
        self.last_event = 0.0

    def handle(self, e):
        sid = e.get("session_id")
        ev = e.get("hook_event_name")
        if not sid or not ev:
            return None
        ts = e.get("_ts") or time.time()
        aid = e.get("agent_id") or "main"
        with self.lock:
            self.last_event = ts
            agents = self.sessions.setdefault(sid, {})

            def upd(key, **kw):
                a = agents.setdefault(key, {"status": None, "tool": None})
                a.update(kw, ts=ts)

            if ev == "PreToolUse":
                name = e.get("tool_name")
                upd(aid, status="waiting" if name in WAITING_TOOLS else "running",
                    tool={"name": name, "brief": tool_brief(e.get("tool_input")), "ts": ts})
            elif ev in ("PostToolUse", "PostToolUseFailure"):
                upd(aid, status="running", tool=None)
            elif ev == "PermissionRequest":
                upd(aid, status="waiting")
            elif ev == "Notification":
                kind = e.get("notification_type") or ""
                upd(aid, status="idle" if kind == "idle_prompt" else "waiting")
            elif ev == "UserPromptSubmit":
                upd("main", status="running", tool=None)
            elif ev == "Stop":
                upd("main", status="idle", tool=None)
            elif ev == "SubagentStart":
                upd(e.get("agent_id") or "?", status="running", tool=None)
            elif ev == "SubagentStop":
                upd(e.get("agent_id") or "?", status="done", tool=None)
            elif ev == "SessionStart":
                upd("main", status="idle", tool=None)
            elif ev == "SessionEnd":
                upd("main", status="ended", tool=None)
            self.log.append({"ts": ts, "session": sid, "event": ev, "agent": aid,
                             "tool": e.get("tool_name")})
        return sid

    def get(self, sid, aid):
        with self.lock:
            a = self.sessions.get(sid, {}).get(aid)
            return dict(a) if a else None


LIVE = LiveState()


def merged_status(tr, live, is_sub, now):
    status = tr.jsonl_status(is_sub, now)
    tool = tr.current_tool()
    if live and live.get("status"):
        # hooks win unless the transcript has clearly moved on since the event
        if not (live["status"] == "running" and status in ("done", "idle", "stale")
                and tr.mtime > live["ts"] + 5):
            status = live["status"]
            tool = live.get("tool") if live.get("tool") is not None else (
                tool if status in ("running", "waiting") else None)
    if status in ("done", "idle", "ended", "stale"):
        tool = None
    return status, tool


# --------------------------------------------------------------------------- API

def node_dict(tr, node_id, parent, meta, sid, now):
    is_sub = node_id != "main"
    status, tool = merged_status(tr, LIVE.get(sid, node_id), is_sub, now)
    label = (meta.get("description") if is_sub else None) or tr.custom_title or tr.title \
        or (tr.last_prompt or "")[:80] or node_id
    return {
        "id": node_id, "parent": parent, "label": label,
        "agent_type": meta.get("agentType") if is_sub else "main",
        "background": meta.get("requestShape") == "background",
        "status": status, "tool": tool,
        "started": tr.first_ts, "last": tr.last_ts, "mtime": tr.mtime,
        "out_tokens": tr.out_tokens, "context_tokens": tr.context_tokens,
        "tool_calls": tr.tool_calls, "errors": tr.errors,
        "top_tools": tr.tools.most_common(4), "model": tr.model,
    }


def session_summary(path, now):
    tr = get_transcript(path)
    subs = subagent_files(path)
    sid = path.stem
    status, _ = merged_status(tr, LIVE.get(sid, "main"), False, now)
    running_subs = 0
    for f in subs:
        try:
            if now - f.stat().st_mtime < RUNNING_GRACE:
                s, _ = merged_status(get_transcript(f), LIVE.get(sid, f.stem[6:]), True, now)
                running_subs += s in ("running", "waiting")
        except OSError:
            pass
    if running_subs and status in ("idle", "stale"):
        status = "running"
    return {
        "id": sid, "project": path.parent.name, "cwd": tr.cwd,
        "title": tr.custom_title or tr.title or (tr.last_prompt or "")[:80] or sid,
        "status": status, "mtime": tr.mtime, "started": tr.first_ts,
        "entrypoint": tr.entrypoint, "subagents": len(subs), "running_subagents": running_subs,
    }


def api_sessions(q):
    limit = int(q.get("limit", ["40"])[0])
    now = time.time()
    out = []
    for _, p in main_session_files()[:limit]:
        try:
            out.append(session_summary(p, now))
        except OSError:
            pass
    return {"sessions": out, "hooks_last": LIVE.last_event, "now": now}


def api_session(q):
    sid = q.get("id", [""])[0]
    path = find_session(sid)
    if not path:
        return None
    now = time.time()
    main = get_transcript(path)
    agents = {"main": (main, {})}
    for f in subagent_files(path):
        agents[f.stem[len("agent-"):]] = (get_transcript(f), read_meta(f))
    # parent = whichever transcript issued the Agent tool call that spawned it
    owner = {}
    for aid, (tr, _) in agents.items():
        for tid in tr.agent_calls:
            owner[tid] = aid
    nodes = []
    for aid, (tr, meta) in agents.items():
        parent = None if aid == "main" else owner.get(meta.get("toolUseId"), "main")
        if parent == aid:
            parent = "main"
        nodes.append(node_dict(tr, aid, parent, meta, sid, now))
    return {"session": session_summary(path, now), "nodes": nodes,
            "hooks_last": LIVE.last_event, "now": now}


def transcript_path(sid, aid):
    path = find_session(sid)
    if not path:
        return None
    if aid in ("", "main"):
        return path
    if "/" in aid or ".." in aid:
        return None
    p = path.with_suffix("") / "subagents" / f"agent-{aid}.jsonl"
    return p if p.exists() else None


def api_transcript(q):
    path = transcript_path(q.get("session", [""])[0], q.get("agent", ["main"])[0])
    if not path:
        return None
    full = q.get("full", ["0"])[0] == "1"
    size = path.stat().st_size
    truncated = False
    with open(path, "rb") as f:
        if not full and size > TAIL_BYTES:
            f.seek(size - TAIL_BYTES)
            f.readline()
            truncated = True
        raw = f.read()
    items = []
    for line in raw.split(b"\n"):
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        items.extend(render_entry(d))
    if not full and len(items) > TAIL_ITEMS:
        items = items[-TAIL_ITEMS:]
        truncated = True
    return {"items": items, "truncated": truncated, "size": size}


def render_entry(d):
    t = d.get("type")
    ts = parse_ts(d.get("timestamp"))
    msg = d.get("message") if isinstance(d.get("message"), dict) else {}
    content = msg.get("content")
    out = []
    if t == "system":
        text = d.get("content") or d.get("subtype") or ""
        if d.get("subtype") == "compact_boundary":
            text = "—— 上下文已压缩 ——"
        if text:
            out.append({"role": "system", "text": str(text)[:2000], "ts": ts})
    elif t == "user":
        if d.get("isMeta"):
            return out
        if isinstance(content, str):
            out.append({"role": "user", "text": content[:20000], "ts": ts})
        for b in content if isinstance(content, list) else []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text":
                out.append({"role": "user", "text": b.get("text", "")[:20000], "ts": ts})
            elif b.get("type") == "tool_result":
                out.append({"role": "tool_result", "id": b.get("tool_use_id"),
                            "text": flatten(b.get("content"))[:8000],
                            "is_error": bool(b.get("is_error")), "ts": ts})
    elif t == "assistant":
        for b in content if isinstance(content, list) else []:
            if not isinstance(b, dict):
                continue
            bt = b.get("type")
            if bt == "text" and b.get("text", "").strip():
                out.append({"role": "assistant", "text": b["text"][:20000], "ts": ts})
            elif bt == "thinking" and b.get("thinking", "").strip():
                out.append({"role": "thinking", "text": b["thinking"][:8000], "ts": ts})
            elif bt == "tool_use":
                inp = b.get("input")
                out.append({"role": "tool_use", "id": b.get("id"), "name": b.get("name"),
                            "brief": tool_brief(inp),
                            "input": json.dumps(inp, ensure_ascii=False, indent=1)[:8000],
                            "ts": ts})
    return out


# --------------------------------------------------------------------------- SSE + watcher

_clients = set()
_clients_lock = threading.Lock()


def broadcast(obj):
    data = json.dumps(obj, ensure_ascii=False)
    with _clients_lock:
        for q in list(_clients):
            try:
                q.put_nowait(data)
            except queue.Full:
                pass


def watcher(interval=1.0, horizon=86400):
    """Poll transcript mtimes; push `changed` events for sessions that were written."""
    seen = {}
    while True:
        changed = set()
        now = time.time()
        for mtime, p in main_session_files()[:60]:
            files = [p]
            if now - mtime < horizon:
                files += subagent_files(p)
            for f in files:
                try:
                    st = f.stat()
                except OSError:
                    continue
                key = (st.st_mtime, st.st_size)
                if seen.get(f) != key:
                    if f in seen:
                        changed.add(p.stem)
                    seen[f] = key
        if changed:
            broadcast({"type": "changed", "sessions": sorted(changed)})
        time.sleep(interval)


class Handler(BaseHTTPRequestHandler):
    server_version = "AgentView/0.1"

    def log_message(self, fmt, *args):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if u.path in ("/", "/index.html"):
                body = (HERE / "static" / "index.html").read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            elif u.path == "/events":
                self._sse()
            elif u.path == "/api/sessions":
                self._json(api_sessions(q))
            elif u.path in ("/api/session", "/api/transcript"):
                res = (api_session if u.path == "/api/session" else api_transcript)(q)
                self._json(res if res is not None else {"error": "not found"},
                           200 if res is not None else 404)
            elif u.path == "/api/hooklog":
                with LIVE.lock:
                    self._json({"events": list(LIVE.log)})
            else:
                self._json({"error": "not found"}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        if urlparse(self.path).path != "/event":
            return self._json({"error": "not found"}, 404)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            e = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._json({"error": "bad json"}, 400)
        sid = LIVE.handle(e)
        self._json({"ok": True})
        if sid:
            broadcast({"type": "hook", "session": sid, "event": e.get("hook_event_name"),
                       "agent": e.get("agent_id") or "main"})

    def _sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        q = queue.Queue(maxsize=200)
        with _clients_lock:
            _clients.add(q)
        try:
            self.wfile.write(b"data: {\"type\":\"hello\"}\n\n")
            self.wfile.flush()
            while True:
                try:
                    data = q.get(timeout=15)
                    self.wfile.write(f"data: {data}\n\n".encode())
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            with _clients_lock:
                _clients.discard(q)


def agent_view_running(host, port):
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/hooklog", timeout=2) as r:
            return "events" in json.load(r)
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default=os.environ.get("AGENT_VIEW_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("AGENT_VIEW_PORT", "7777")))
    ap.add_argument("--reuse", action="store_true",
                    help="if an Agent View already serves the port, wait on it instead of failing, "
                         "and take the port over once it goes away")
    args = ap.parse_args()
    while True:
        try:
            httpd = ThreadingHTTPServer((args.host, args.port), Handler)
            break
        except OSError as err:
            if not (args.reuse and agent_view_running(args.host, args.port)):
                sys.exit(f"agent-view: cannot bind {args.host}:{args.port}: {err}")
            print(f"agent-view: already running on {args.host}:{args.port}, standing by", flush=True)
            try:
                while agent_view_running(args.host, args.port):
                    time.sleep(5)
            except KeyboardInterrupt:
                return
    httpd.daemon_threads = True
    threading.Thread(target=watcher, daemon=True).start()
    try:
        import autopreview  # Browser pane entry for new Desktop projects; optional
        threading.Thread(target=autopreview.watch_desktop, daemon=True).start()
    except Exception as err:
        print(f"agent-view: autopreview watcher disabled: {err}", flush=True)
    print(f"agent-view: http://{args.host}:{args.port}  (transcripts: {PROJECTS})", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
