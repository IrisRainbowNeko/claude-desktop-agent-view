"""Small OS adapters shared by the hooks, server and SSH mirror."""
import os
import subprocess
import sys
from pathlib import Path, PureWindowsPath


def state_dir():
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "agent-view"
    if sys.platform == "darwin":
        return Path(os.environ.get("XDG_STATE_HOME", Path.home() / "Library" / "Logs" / "Agent View"))
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))


def cache_dir():
    if sys.platform == "win32":
        return state_dir() / "cache"
    if sys.platform == "darwin":
        return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / "Library" / "Caches")) / "agent-view"
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "agent-view"


def background_options(detached=False):
    """Hide Windows subprocess consoles; detach long-lived servers from their hook."""
    if sys.platform == "win32":
        flags = subprocess.CREATE_NO_WINDOW
        if detached:
            flags |= subprocess.CREATE_NEW_PROCESS_GROUP
        return {"creationflags": flags, "close_fds": True}
    return {"start_new_session": detached, "close_fds": True}


def is_worktree(path):
    # Desktop records may use either separator, even when inspected on another OS.
    parts = PureWindowsPath(str(path)).parts
    return any(a == ".claude" and b == "worktrees" for a, b in zip(parts, parts[1:]))
