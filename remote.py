"""Mirror the transcripts of Claude Desktop SSH sessions, so they show up in Agent View.

Desktop records every Code session in claude-code-sessions/*/*/local_<id>.json. For SSH
sessions that record has sshConfig.sshHost (an ssh alias that works non-interactively, since
Desktop itself connects with it) and sshRemoteTranscriptPath. For each host with recently
active sessions this keeps one `ssh <host> python3 ...` running. The remote side is AGENT
below, passed on the command line, so nothing is installed on the server and no port is opened
there. It streams the new bytes of each session transcript and its subagent files, and they
are appended to a local mirror, MIRROR/<host>/projects/<project>/..., which server.py reads
like ~/.claude/projects.

Needs python3 on the server and key/agent based ssh (BatchMode). Hook events are not
forwarded, so remote status is inferred from transcripts only.
"""
import base64
import json
import os
import subprocess
import threading
import time
from pathlib import Path

MIRROR = Path(os.environ.get("AGENT_VIEW_REMOTE_CACHE",
                             Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
                             / "agent-view" / "remote"))
HORIZON = 86400          # mirror sessions active in the last day
RESCAN = 5               # s between reads of Desktop's session records
MAX_CHUNK = 4 << 20      # bytes per file per remote poll

# Runs on the server. stdin: JSON lines {"watch": {remote_transcript: {file: offset}}}.
# stdout: JSON lines {"f": file, "o": offset, "d": base64 bytes}. Exits when stdin closes.
AGENT = r'''
import base64, glob, json, os, select, sys, time
watch, offs = {}, {}
while True:
    r, _, _ = select.select([sys.stdin], [], [], 1.0)
    if r:
        line = sys.stdin.readline()
        if not line:
            break
        watch = json.loads(line)["watch"]
        offs = {f: o for t in watch.values() for f, o in t.items()}
    for t in watch:
        base = t[:-len(".jsonl")]
        for f in [t] + glob.glob(base + "/subagents/*.jsonl") + glob.glob(base + "/subagents/*.meta.json"):
            try:
                size = os.path.getsize(f)
            except OSError:
                continue
            o = offs.get(f, 0)
            if size < o:
                o = 0
            if size == o:
                continue
            with open(f, "rb") as fh:
                fh.seek(o)
                d = fh.read(4 << 20)
            sys.stdout.write(json.dumps({"f": f, "o": o, "d": base64.b64encode(d).decode()}) + "\n")
            offs[f] = o + len(d)
    sys.stdout.flush()
'''


def _desktop_session_dirs():
    try:
        from autopreview import desktop_session_dirs
        return desktop_session_dirs()
    except Exception:
        return []


def ssh_sessions(now):
    """{host: [remote transcript path]} for SSH sessions active within HORIZON."""
    out = {}
    for root in _desktop_session_dirs():
        for f in root.glob("*/*/local_*.json"):
            try:
                if now - f.stat().st_mtime > HORIZON:
                    continue
                d = json.loads(f.read_text())
            except (OSError, ValueError):
                continue
            host = (d.get("sshConfig") or {}).get("sshHost")
            path = d.get("sshRemoteTranscriptPath")
            last = max(d.get("lastActivityAt") or 0, d.get("createdAt") or 0) / 1000
            if host and path and path.endswith(".jsonl") and not d.get("isArchived") \
                    and now - last < HORIZON and "/" not in host:
                out.setdefault(host, []).append(path)
    return out


class HostSync:
    """One ssh connection streaming the watched transcripts of one host into the mirror."""

    def __init__(self, host, on_change):
        self.host, self.on_change = host, on_change
        self.root = MIRROR / host / "projects"
        self.watch = []
        self.proc = None
        self.error = None
        self.connected = False
        self.lock = threading.Lock()
        self.stop = False
        threading.Thread(target=self._run, daemon=True).start()

    def local(self, remote_file):
        """Mirror path: keep the path below the remote projects/<project>/ directory."""
        parts = Path(remote_file).parts
        i = len(parts) - 1 - parts[::-1].index("projects") if "projects" in parts else len(parts) - 2
        rel = Path(*parts[i + 1:])
        if ".." in rel.parts or rel.is_absolute():
            raise ValueError(remote_file)
        return self.root / rel

    def set_watch(self, paths):
        paths = sorted(set(paths))
        with self.lock:
            if paths == self.watch:
                return
            self.watch = paths
        self._send_watch()

    def _offsets(self, t):
        offs = {}
        for f in [t]:
            p = self.local(f)
            offs[f] = p.stat().st_size if p.exists() else 0
        sub = self.local(t).with_suffix("") / "subagents"
        base = t[:-len(".jsonl")] + "/subagents/"
        if sub.is_dir():
            for p in sub.iterdir():
                offs[base + p.name] = p.stat().st_size
        return offs

    def _send_watch(self):
        with self.lock:
            proc, watch = self.proc, list(self.watch)
        if not proc or proc.poll() is not None:
            return
        try:
            msg = {"watch": {t: self._offsets(t) for t in watch}}
            proc.stdin.write((json.dumps(msg) + "\n").encode())
            proc.stdin.flush()
        except (OSError, ValueError):
            pass

    def _run(self):
        delay = 5
        while not self.stop:
            if not self.watch:
                time.sleep(2)
                continue
            code = base64.b64encode(AGENT.encode()).decode()
            cmd = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
                   "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=3", "-C", self.host,
                   f"python3 -u -c 'import base64,sys;exec(base64.b64decode(sys.argv[1]))' {code}"]
            started = time.time()
            try:
                proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, start_new_session=True)
            except OSError as err:
                self.error = f"ssh: {err}"
                time.sleep(60)
                continue
            with self.lock:
                self.proc = proc
            self._send_watch()
            err_lines = []
            threading.Thread(target=lambda: err_lines.extend(
                l.decode(errors="replace").strip() for l in proc.stderr), daemon=True).start()
            for line in proc.stdout:
                self.connected, self.error = True, None
                try:
                    m = json.loads(line)
                    self._apply(m["f"], m["o"], base64.b64decode(m["d"]))
                except (ValueError, KeyError, OSError):
                    continue
            proc.wait()
            self.connected = False
            with self.lock:
                self.proc = None
            if self.stop:
                break
            if not self.watch:
                continue  # closed on purpose (nothing left to watch)
            msg = next((l for l in reversed(err_lines) if l), f"exit {proc.returncode}")
            self.error = msg[:200]
            delay = 5 if time.time() - started > 120 else min(delay * 2, 300)
            time.sleep(delay)

    def _apply(self, remote_file, offset, data):
        p = self.local(remote_file)
        p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with open(p, "r+b" if p.exists() else "wb") as f:
            f.seek(offset)
            f.write(data)
            f.truncate()
        self.on_change()

    def close(self):
        self.stop = True
        with self.lock:
            self.watch = []
            proc = self.proc
        if proc and proc.poll() is None:
            try:
                proc.stdin.close()  # the agent exits on EOF
            except OSError:
                pass
            try:
                proc.wait(5)
            except subprocess.TimeoutExpired:
                proc.kill()


HOSTS = {}


def status():
    return [{"host": h, "connected": s.connected, "error": s.error, "sessions": len(s.watch)}
            for h, s in sorted(HOSTS.items())]


def roots():
    """[(host, mirrored projects dir)] for every host that has been mirrored."""
    try:
        return [(d.name, d / "projects") for d in sorted(MIRROR.iterdir()) if (d / "projects").is_dir()]
    except OSError:
        return []


def run(on_change=lambda: None):
    """Keep one HostSync per host with recent SSH sessions. Blocking; run in a thread."""
    if os.environ.get("AGENT_VIEW_REMOTE", "1") == "0":
        return
    MIRROR.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(MIRROR.parent, 0o700)
    while True:
        wanted = ssh_sessions(time.time())
        for host in list(HOSTS):
            if host not in wanted:
                HOSTS.pop(host).close()
        for host, paths in wanted.items():
            if host not in HOSTS:
                HOSTS[host] = HostSync(host, on_change)
            HOSTS[host].set_watch(paths)
        time.sleep(RESCAN)
