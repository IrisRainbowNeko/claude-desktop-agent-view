import json
import os
from pathlib import Path, PureWindowsPath
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import autopreview
import install_hooks
import platform_support
import remote


class PlatformTests(unittest.TestCase):
    def test_windows_hooks_bypass_shell_and_uninstall_preserves_other_hooks(self):
        hook = PureWindowsPath("C:/Users/猫/My Agent's View/hook.py")
        auto = hook.with_name("autopreview.py")
        other = {"type": "command", "command": "echo keep me"}
        settings = {"hooks": {"SessionStart": [{"hooks": [other]}]}}
        with patch.object(install_hooks.sys, "platform", "win32"), \
                patch.object(install_hooks.sys, "executable", "C:/Program Files/Python/python.exe"), \
                patch.object(install_hooks, "HOOK", hook), patch.object(install_hooks, "AUTOPREVIEW", auto):
            install_hooks.install(settings)
            install_hooks.install(settings)
            hooks = [h for g in settings["hooks"]["SessionStart"] for h in g["hooks"]]
            self.assertEqual(len(hooks), 3)
            own = [h for h in hooks if "args" in h]
            self.assertEqual([h["args"] for h in own], [[str(hook)], [str(auto)]])
            self.assertTrue(all(h["command"] == "C:/Program Files/Python/python.exe" for h in own))
            install_hooks.strip(settings)
            self.assertEqual(settings, {"hooks": {"SessionStart": [{"hooks": [other]}]}})

    def test_installer_preserves_utf8_settings_and_backup(self):
        with tempfile.TemporaryDirectory(prefix="agent view ") as temp:
            settings = Path(temp) / "settings.json"
            original = b'\xef\xbb\xbf' + json.dumps({"env": {"NOTE": "猫 café"}}, ensure_ascii=False).encode()
            settings.write_bytes(original)
            subprocess.run([sys.executable, "install_hooks.py", "--settings", str(settings)],
                           check=True, capture_output=True)
            self.assertEqual(json.loads(settings.read_text(encoding="utf8"))["env"], {"NOTE": "猫 café"})
            self.assertEqual(next(Path(temp).glob("*.bak-*")).read_bytes(), original)
            subprocess.run([sys.executable, "install_hooks.py", "--uninstall", "--settings", str(settings)],
                           check=True, capture_output=True)
            self.assertEqual(json.loads(settings.read_text(encoding="utf8")), {"env": {"NOTE": "猫 café"}})

    def test_untracked_unicode_preview_keeps_other_entries_and_is_ignored(self):
        with tempfile.TemporaryDirectory(prefix="agent view ") as temp:
            root = Path(temp).resolve()
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            folder = root / "猫 project"
            folder.mkdir()
            self.assertTrue(autopreview.ensure_config(folder))
            config = folder / ".claude" / "launch.json"
            data = json.loads(config.read_text(encoding="utf8"))
            self.assertEqual(data["configurations"][0]["runtimeExecutable"], sys.executable)
            subprocess.run(["git", "-C", str(root), "check-ignore", "-q", str(config)], check=True)
            config.write_text(json.dumps({"configurations": [{"name": "猫"}]}, ensure_ascii=False), encoding="utf-8-sig")
            self.assertTrue(autopreview.ensure_config(folder))
            self.assertEqual(json.loads(config.read_text(encoding="utf8"))["configurations"][0], {"name": "猫"})

    def test_tracked_launch_configuration_is_not_modified(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            config = root / ".claude" / "launch.json"
            config.parent.mkdir()
            config.write_text('{"configurations": []}', encoding="utf8")
            subprocess.run(["git", "-C", str(root), "add", "-f", str(config)], check=True)
            before = config.read_bytes()
            self.assertFalse(autopreview.ensure_config(root))
            self.assertEqual(config.read_bytes(), before)

    def test_native_windows_paths_and_worktree_filter(self):
        for path in ["C:\\repo\\.claude\\worktrees\\branch", "/repo/.claude/worktrees/branch"]:
            self.assertTrue(platform_support.is_worktree(path))
        self.assertFalse(platform_support.is_worktree("C:/repo/worktrees/project"))
        with patch.object(platform_support.sys, "platform", "win32"), \
                patch.dict(os.environ, {"LOCALAPPDATA": "C:/Users/猫/AppData/Local"}):
            self.assertEqual(platform_support.state_dir().as_posix(), "C:/Users/猫/AppData/Local/agent-view")
            self.assertEqual(platform_support.cache_dir(), platform_support.state_dir() / "cache")

    def test_remote_posix_paths_cannot_become_windows_drives_or_traversals(self):
        sync = remote.HostSync.__new__(remote.HostSync)
        sync.root = Path("mirror")
        self.assertEqual(sync.local("/home/user/.claude/projects/project/session.jsonl"),
                         Path("mirror/project/session.jsonl"))
        for remote_file in ["/projects/../secret", "/projects/C:/secret", "/projects/a\\b"]:
            with self.assertRaises(ValueError):
                sync.local(remote_file)

    @unittest.skipUnless(sys.platform == "win32", "Windows process flags")
    def test_windows_background_flags_and_process_survives_launcher(self):
        options = platform_support.background_options(detached=True)
        self.assertTrue(options["creationflags"] & subprocess.CREATE_NO_WINDOW)
        self.assertTrue(options["creationflags"] & subprocess.CREATE_NEW_PROCESS_GROUP)
        with tempfile.TemporaryDirectory() as temp:
            result = Path(temp) / "result.txt"
            code = "import pathlib,sys,time; time.sleep(.2); pathlib.Path(sys.argv[1]).write_text('ok')"
            child = subprocess.Popen([sys.executable, "-c", code, str(result)],
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, **options)
            self.assertEqual(child.wait(timeout=10), 0)
            self.assertEqual(result.read_text(), "ok")


if __name__ == "__main__":
    unittest.main()
