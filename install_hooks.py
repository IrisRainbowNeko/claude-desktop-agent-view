#!/usr/bin/env python3
"""Register (or remove) the Agent View hooks in ~/.claude/settings.json.

    python3 install_hooks.py              # install / update
    python3 install_hooks.py --uninstall  # remove
    python3 install_hooks.py --dry-run    # print the resulting settings only

A timestamped backup of settings.json is written before any change. Only hook
entries whose command points at this directory's hook.py / autopreview.py are touched.
"""
import argparse
import json
import os
import shlex
import shutil
import sys
import time
from pathlib import Path

HOOK = Path(__file__).resolve().parent / "hook.py"
AUTOPREVIEW = HOOK.with_name("autopreview.py")
SETTINGS = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")) / "settings.json"
TOOL_EVENTS = ["PreToolUse", "PostToolUse", "PostToolUseFailure", "PermissionRequest"]
OTHER_EVENTS = ["SessionStart", "SessionEnd", "UserPromptSubmit", "Notification",
                "Stop", "SubagentStart", "SubagentStop"]


def ours(hook):
    cmd = str(hook.get("command", "")) if isinstance(hook, dict) else ""
    return str(HOOK) in cmd or str(AUTOPREVIEW) in cmd


def strip(settings):
    hooks = settings.get("hooks") or {}
    for event in list(hooks):
        groups = []
        for g in hooks[event] or []:
            kept = [h for h in g.get("hooks", []) if not ours(h)]
            if kept:
                groups.append({**g, "hooks": kept})
        if groups:
            hooks[event] = groups
        else:
            del hooks[event]
    if hooks:
        settings["hooks"] = hooks
    else:
        settings.pop("hooks", None)


def install(settings):
    strip(settings)
    hooks = settings.setdefault("hooks", {})
    cmd = f"{shlex.quote(sys.executable)} {shlex.quote(str(HOOK))}"
    entry = {"type": "command", "command": cmd, "async": True, "timeout": 5}
    for event in TOOL_EVENTS:
        hooks.setdefault(event, []).append({"matcher": "*", "hooks": [dict(entry)]})
    for event in OTHER_EVENTS:
        hooks.setdefault(event, []).append({"hooks": [dict(entry)]})
    # Synchronous: in "auto" mode its stdout (the "open the preview" instruction) must reach the context.
    auto = f"{shlex.quote(sys.executable)} {shlex.quote(str(AUTOPREVIEW))}"
    hooks["SessionStart"].append({"hooks": [{"type": "command", "command": auto, "timeout": 10}]})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--settings", type=Path, default=SETTINGS)
    args = ap.parse_args()

    settings = {}
    if args.settings.exists():
        try:
            settings = json.loads(args.settings.read_text() or "{}")
        except ValueError as err:
            sys.exit(f"{args.settings} is not valid JSON ({err}); fix it first.")

    (strip if args.uninstall else install)(settings)
    text = json.dumps(settings, ensure_ascii=False, indent=2) + "\n"
    if args.dry_run:
        print(text)
        return
    if args.settings.exists():
        backup = args.settings.with_name(f"settings.json.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(args.settings, backup)
        print(f"backup: {backup}")
    args.settings.parent.mkdir(parents=True, exist_ok=True)
    args.settings.write_text(text)
    print(("removed Agent View hooks from " if args.uninstall else "installed Agent View hooks in ")
          + str(args.settings))
    if not args.uninstall:
        print("Sessions that are already running may need a restart to pick up the hooks.")


if __name__ == "__main__":
    main()
