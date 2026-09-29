#!/usr/bin/env python3
"""SessionStart hook: make Agent View available in the Desktop Browser pane of every project.

The pane only lists configs from the session project's .claude/launch.json, so this adds an
"agent-view" entry there (running preview.py from this repo) when it is missing, so it can be
started from the pane with one click.

AGENT_VIEW_AUTOPREVIEW: "config" (default) only add the config; "auto" also ask Claude to open
the preview before its first reply (only a tool call can open the pane by itself); "0" do nothing.

Runs synchronously (in "auto" mode its stdout becomes session context), so it stays quick and
never fails: any problem just means no config and no instruction. Only acts in Claude Desktop sessions.
A launch.json tracked by git is never modified; a newly created one is added to
.git/info/exclude so it does not show up in git status.

Desktop only starts a session's process (and so this hook) once a message is sent, so
`python3 autopreview.py --all` adds the config to every project that has transcripts under
~/.claude/projects, making the entry available before you ever type in that project
(`--all --dry-run` lists them first). server.py also runs watch_desktop(), which adds the
config as soon as Desktop creates a session in a new folder, before its first message.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

NAME = "agent-view"
PREVIEW = Path(__file__).resolve().parent / "preview.py"
CONTEXT = (
    'Agent View (a live dashboard of this session and its subagents) is available as the Browser '
    f'preview "{NAME}". Before your first reply in this session, call preview_start with name '
    f'"{NAME}" once, without verifying, screenshotting or mentioning it unless it fails, then handle '
    "the user's message as usual. If preview_start is not available, skip this."
)


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=3)


def exclude_from_git(path):
    """Keep a launch.json we created out of `git status`, without touching .gitignore."""
    top = git(path.parent, "rev-parse", "--show-toplevel")
    if top.returncode or not git(path.parent, "check-ignore", "-q", str(path)).returncode:
        return  # not a repo, or already ignored
    info = Path(git(path.parent, "rev-parse", "--git-path", "info/exclude").stdout.strip())
    if not info.is_absolute():
        info = path.parent / info
    rel = "/" + path.relative_to(Path(top.stdout.strip()).resolve()).as_posix()
    info.parent.mkdir(parents=True, exist_ok=True)
    with open(info, "a") as f:
        f.write(f"\n# added by Agent View autopreview\n{rel}\n")


def ensure_config(cwd):
    """True if cwd/.claude/launch.json has (or now has) the agent-view config."""
    path = cwd / ".claude" / "launch.json"
    entry = {"name": NAME, "runtimeExecutable": sys.executable, "runtimeArgs": [str(PREVIEW)],
             "port": 7778, "autoPort": True}
    if path.exists():
        try:
            data = json.loads(path.read_text() or "{}")
            configs = data.setdefault("configurations", [])
        except (ValueError, AttributeError):
            return False  # comments / odd content: leave the user's file alone
        if any(isinstance(c, dict) and c.get("name") == NAME for c in configs):
            return True
        if git(cwd, "ls-files", "--error-unmatch", str(path)).returncode == 0:
            return False  # committed file: don't create a surprise diff
        configs.append(entry)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        return True
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"version": "0.0.1", "configurations": [entry]}, indent=2) + "\n")
    try:
        exclude_from_git(path)
    except Exception:
        pass
    return True


def known_projects():
    """Working directories of past sessions, from the cwd field of their transcripts."""
    root = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")) / "projects"
    dirs = set()
    for proj in root.iterdir() if root.is_dir() else []:
        for f in sorted(proj.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:3]:
            with open(f, errors="replace") as fh:
                for _, line in zip(range(50), fh):
                    if '"cwd"' in line:
                        try:
                            dirs.add(json.loads(line)["cwd"])
                            break
                        except (ValueError, KeyError):
                            pass
    home = Path.home().resolve()
    return sorted(p for p in map(Path, dirs)
                  if p.is_dir() and p.resolve() != home and "/.claude/worktrees/" not in str(p))


def backfill(dry_run):
    for d in known_projects():
        if dry_run:
            print(d)
            continue
        try:
            ok = ensure_config(d)
        except Exception as err:
            ok = err
        print(("ok      " if ok is True else f"skipped ({ok or 'launch.json committed or not plain JSON'}) ") + str(d))


def desktop_session_dirs():
    """Where Claude Desktop keeps one local_<id>.json per Code session (with its cwd)."""
    env = os.environ.get("AGENT_VIEW_DESKTOP_SESSIONS")
    if env:
        return [Path(env)]
    bases = [Path.home() / ".config", Path.home() / "Library" / "Application Support"]
    if os.environ.get("APPDATA"):
        bases.append(Path(os.environ["APPDATA"]))
    return [d for b in bases for d in b.glob("Claude*/claude-code-sessions") if d.is_dir()]


def watch_desktop(interval=3.0):
    """Add the config to the folder of every new Desktop session, before its first message."""
    if os.environ.get("AGENT_VIEW_AUTOPREVIEW", "config") == "0":
        return
    done, since = set(), 0.0
    home = Path.home().resolve()
    while True:
        scan_start = time.time()
        for root in desktop_session_dirs():
            for f in root.glob("*/*/local_*.json"):
                try:
                    if f.stat().st_mtime < since:
                        continue
                    d = json.loads(f.read_text())
                    cwd = Path(d.get("cwd") or "")
                    if d.get("isArchived") or not cwd.is_absolute() or cwd in done:
                        continue
                    done.add(cwd)
                    if cwd.is_dir() and cwd.resolve() != home and "/.claude/worktrees/" not in str(cwd):
                        ensure_config(cwd)
                except Exception:
                    pass
        since = scan_start - 1
        time.sleep(interval)


def main():
    mode = os.environ.get("AGENT_VIEW_AUTOPREVIEW", "config")
    desktop = os.environ.get("CLAUDE_CODE_ENTRYPOINT", "").startswith("claude-desktop") \
        or "CLAUDE_CODE_DESKTOP_APP_VERSION" in os.environ
    if mode == "0" or not desktop:
        return
    data = json.loads(sys.stdin.buffer.read() or b"{}")
    cwd = Path(data.get("cwd") or os.getcwd()).resolve()
    if not ensure_config(cwd) or mode != "auto" or data.get("source") == "compact":
        return
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                             "additionalContext": CONTEXT}}))


if __name__ == "__main__":
    if "--all" in sys.argv:
        backfill("--dry-run" in sys.argv)
        sys.exit(0)
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
