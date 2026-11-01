"""Incremental, memory-bounded metadata index for the entire session history.

Indexing runs in a worker so an old, large transcript never blocks navigation.
Unlike Transcript, entries retain no tool inputs, messages or agent-call trees.
"""
import hashlib
import json
import ntpath
import posixpath
import re
import threading
import time
from datetime import datetime


def timestamp(value):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return None


def project_identity(cwd, host, fallback=""):
    cwd = cwd or fallback
    windows = bool(re.match(r"^[a-zA-Z]:[\\/]", cwd) or cwd.startswith("\\\\"))
    normalized = ntpath.normcase(ntpath.normpath(cwd)) if windows else posixpath.normpath(cwd)
    key = json.dumps([host or "", normalized], ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(key.encode()).hexdigest()


class Metadata:
    def __init__(self):
        self.offset = 0
        self.identity = None
        self.mtime = 0
        self.cwd = self.title = self.custom_title = self.last_prompt = self.entrypoint = None
        self.started = None
        self.kind = None
        self.open_tools = {}

    def feed(self, data):
        if not isinstance(data, dict):
            return
        kind = data.get("type")
        if isinstance(data.get("cwd"), str) and data["cwd"]:
            self.cwd = data["cwd"]
        if isinstance(data.get("entrypoint"), str) and data["entrypoint"]:
            self.entrypoint = data["entrypoint"]
        if data.get("timestamp") and self.started is None:
            self.started = timestamp(data["timestamp"])
        if kind == "ai-title":
            self.title = data["aiTitle"] if isinstance(data.get("aiTitle"), str) and data["aiTitle"] else self.title
        elif kind == "custom-title":
            self.custom_title = data["customTitle"] if isinstance(data.get("customTitle"), str) and data["customTitle"] else self.custom_title
        elif kind == "last-prompt":
            if isinstance(data.get("lastPrompt"), str):
                self.last_prompt = data["lastPrompt"][:80]
        msg = data.get("message")
        if not isinstance(msg, dict):
            return
        if kind == "assistant":
            content = msg.get("content")
            tools = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"] if isinstance(content, list) else []
            for tool in tools:
                if isinstance(tool.get("id"), str):
                    self.open_tools[tool["id"]] = {"name": tool.get("name"), "ts": timestamp(data.get("timestamp"))}
            tool = bool(tools)
            self.kind = "tool" if tool or msg.get("stop_reason") == "tool_use" else (
                "end" if msg.get("stop_reason") else "partial")
        elif kind == "user" and not data.get("isMeta"):
            self.kind = "user"
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        self.open_tools.pop(block.get("tool_use_id"), None)
            if isinstance(content, str) or (isinstance(content, list) and any(
                    isinstance(block, dict) and block.get("type") == "text" for block in content)):
                self.open_tools.clear()
            if isinstance(content, str) and not self.title and not self.last_prompt:
                self.last_prompt = content[:80]

    def refresh(self, path):
        st = path.stat()
        identity = (st.st_dev, st.st_ino)
        if st.st_size < self.offset or (self.identity is not None and self.identity != identity):
            self.__init__()
        self.identity, self.mtime = identity, st.st_mtime
        if st.st_size == self.offset:
            return
        with path.open("rb") as stream:
            stream.seek(self.offset)
            # Stop at the initial size: a continuously written file must not
            # starve the other projects. Retain an incomplete last line.
            while stream.tell() < st.st_size:
                line = stream.readline()
                if not line.endswith(b"\n"):
                    break
                try:
                    self.feed(json.loads(line))
                except (ValueError, AttributeError, TypeError):
                    pass
                self.offset = stream.tell()

    def status(self, now, is_sub=False):
        tools = [tool for tool in self.open_tools.values() if now - (tool["ts"] or 0) < 1800]
        if tools:
            return "waiting" if any(tool["name"] in ("AskUserQuestion", "ExitPlanMode") for tool in tools) else "running"
        if self.kind == "end":
            return "done" if is_sub else "idle"
        if self.kind in ("user", "tool", "partial"):
            return "running" if now - self.mtime < 300 else "stale"
        return "idle"


class SessionCatalog:
    def __init__(self, files, subagents, host, live):
        self.files, self.subagents, self.host, self.live = files, subagents, host, live
        self.entries = {}
        self.tails = {}
        self.rows = {}
        self.lock = threading.Lock()
        self.worker = None
        self.indexing = True
        self.expected = 0
        self.error = None

    def _sub_status(self, file, now):
        st = file.stat()
        if now - st.st_mtime >= 300:
            return None
        key = (st.st_mtime_ns, st.st_size)
        cached = self.tails.get(file)
        if not cached or cached[0] != key:
            meta = Metadata()
            meta.mtime = st.st_mtime
            with file.open("rb") as stream:
                start = max(0, st.st_size - 65536)
                stream.seek(start)
                if start:
                    stream.readline()
                lines = stream.read(65536).splitlines()
            for line in lines:
                try:
                    meta.feed(json.loads(line))
                except (ValueError, AttributeError, TypeError):
                    pass
            self.tails[file] = (key, meta)
        return self.tails[file][1].status(now, is_sub=True)

    def refresh(self):
        files = self.files()
        with self.lock:
            self.expected = len(files)
        existing = set()
        recent_subs = set()
        now = time.time()
        for _, path in files:
            try:
                meta = self.entries.setdefault(path, Metadata())
                meta.refresh(path)
                subs = self.subagents(path)
                running = 0
                for file in subs:
                    aid = file.stem.removeprefix("agent-")
                    live = self.live.get(path.stem, aid)
                    if live and live.get("status") in ("running", "waiting"):
                        running += 1
                    else:
                        status = self._sub_status(file, now)
                        if status is not None:
                            recent_subs.add(file)
                        running += status in ("running", "waiting") and not (
                            live and live.get("status") in ("done", "ended", "idle"))
                live = self.live.get(path.stem, "main")
                status = (live or {}).get("status") or meta.status(now)
                if running and status in ("idle", "stale", "done", "ended"):
                    status = "running"
                host = self.host(path)
                row = {"id": path.stem, "project": path.parent.name,
                       "project_id": project_identity(meta.cwd, host, path.parent.name),
                       "cwd": meta.cwd, "host": host,
                       "title": meta.custom_title or meta.title or meta.last_prompt or path.stem,
                       "status": status, "mtime": meta.mtime, "started": meta.started,
                       "entrypoint": meta.entrypoint, "subagents": len(subs), "running_subagents": running}
                with self.lock:
                    self.rows[path] = row
                existing.add(path)
            except (OSError, ValueError):
                continue
        self.entries = {p: entry for p, entry in self.entries.items() if p in existing}
        self.tails = {p: tail for p, tail in self.tails.items() if p in recent_subs}
        with self.lock:
            self.rows = {p: row for p, row in self.rows.items() if p in existing}
            self.indexing = False
            self.error = None

    def _run(self):
        while True:
            try:
                self.refresh()
            except Exception as err:
                with self.lock:
                    self.error = str(err)
                    self.indexing = False
            time.sleep(1.5)

    def snapshot(self):
        with self.lock:
            if self.worker is None:
                self.worker = threading.Thread(target=self._run, daemon=True)
                self.worker.start()
            return {"rows": list(self.rows.values()), "indexing": self.indexing,
                    "indexed": len(self.rows), "expected": self.expected, "index_error": self.error}
