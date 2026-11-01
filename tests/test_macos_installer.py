import importlib.util
import json
import os
from pathlib import Path
import plistlib
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import platform_support

SPEC = importlib.util.spec_from_file_location("macos_installer", Path(__file__).resolve().parents[1] / "desktop-patch" / "install-macos.py")
macos = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(macos)


def archive(file, text):
    header = json.dumps({"files": {"example": {"size": len(text), "offset": "0"}}}).encode()
    padding = b"\0" * ((-len(header)) % 4)
    size = 8 + len(header) + len(padding)
    file.write_bytes(struct.pack("<4I", 4, size, size - 4, len(header)) + header + padding + text.encode())


class MacOSTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="agent view mac ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bundle = self.root / "Claude.app"
        self.resources = self.bundle / "Contents" / "Resources"
        self.resources.mkdir(parents=True)
        for name in macos.ARCHIVES:
            archive(self.resources / name, name)
        self.before = macos.archive_hashes(self.bundle)
        self.after = {name: "a" * 64 for name in macos.ARCHIVES}
        self.main = self.bundle / "Contents" / "Info.plist"
        self.helper = self.bundle / "Contents" / "Frameworks" / "Helper.app" / "Contents" / "Info.plist"
        self.helper.parent.mkdir(parents=True)
        self.data = {"CFBundleIdentifier": "org.agent-view.test", "CFBundleExecutable": "Probe",
                     "CFBundlePackageType": "APPL", "LSEnvironment": {"KEEP": "猫"},
                     "ElectronAsarIntegrity": {"Resources/" + name: {"algorithm": "SHA256", "hash": digest}
                                               for name, digest in self.before.items()}}
        self.main.write_bytes(plistlib.dumps(self.data))
        self.helper.write_bytes(plistlib.dumps(self.data, fmt=plistlib.FMT_BINARY))

    def test_both_archives_update_every_helper_and_preserve_metadata(self):
        self.assertEqual(macos.update_integrity(self.bundle, self.before, self.after, python="/opt/猫/python3"), 2)
        for file in (self.main, self.helper):
            data = plistlib.loads(file.read_bytes())
            self.assertEqual(data["CFBundleIdentifier"], self.data["CFBundleIdentifier"])
            for name in macos.ARCHIVES:
                self.assertEqual(data["ElectronAsarIntegrity"]["Resources/" + name]["hash"], self.after[name])
        self.assertTrue(self.helper.read_bytes().startswith(b"bplist"))
        self.assertEqual(plistlib.loads(self.main.read_bytes())["LSEnvironment"], {"KEEP": "猫", "AGENT_VIEW_PYTHON": "/opt/猫/python3"})
        self.assertEqual(macos.update_integrity(self.bundle, self.after, self.before), 2)
        self.assertEqual(plistlib.loads(self.helper.read_bytes()), self.data)

    def test_unknown_helper_hash_refuses_before_any_plist_is_written(self):
        bad = dict(self.data, ElectronAsarIntegrity={"Resources/app.asar": {"algorithm": "SHA256", "hash": "unknown"}})
        self.helper.write_bytes(plistlib.dumps(bad))
        original = self.main.read_bytes()
        with self.assertRaisesRegex(ValueError, "Unreviewed ASAR integrity"):
            macos.update_integrity(self.bundle, self.before, self.after)
        self.assertEqual(self.main.read_bytes(), original)

    def test_failed_signing_never_publishes_destination_or_changes_source(self):
        original = self.main.read_bytes()
        destination = self.root / "Claude Agent View.app"

        def run(*args):
            if str(args[0]).endswith("ditto"):
                shutil.copytree(args[-2], args[-1])
            # The Node call is a no-op here: this test isolates publication on
            # signing failure. Real native bundles are checked by the Node suite.

        with patch.object(macos, "run", side_effect=run), \
                patch.object(macos, "sign_bundle", side_effect=RuntimeError("signing failed")):
            with self.assertRaisesRegex(RuntimeError, "signing failed"):
                macos.patch_copy(self.bundle, destination, "install", "node", self.root / "server.py")
        self.assertFalse(destination.exists())
        self.assertEqual(self.main.read_bytes(), original)
        self.assertEqual(list(self.root.glob(".agent-view-stage-*")), [])

    def test_failed_restore_signing_keeps_existing_copy_and_backup(self):
        state = self.resources / "agent-view-native-patch" / "state.json"
        state.parent.mkdir()
        state.write_text("{}", encoding="utf8")
        original = self.main.read_bytes()
        with patch.object(macos, "run", side_effect=lambda *args: shutil.copytree(args[-2], args[-1])
                          if str(args[0]).endswith("ditto") else None), \
                patch.object(macos, "sign_bundle", side_effect=RuntimeError("signing failed")):
            with self.assertRaisesRegex(RuntimeError, "signing failed"):
                macos.patch_copy(self.bundle, self.bundle, "restore", "node", self.root / "server.py")
        self.assertTrue(state.exists())
        self.assertEqual(self.main.read_bytes(), original)

    def test_macos_paths_and_xdg_overrides(self):
        with patch.object(platform_support.sys, "platform", "darwin"), \
                patch.object(platform_support.Path, "home", return_value=self.root), \
                patch.dict(os.environ, {}, clear=True):
            self.assertEqual(platform_support.state_dir(), Path.home() / "Library" / "Logs" / "Agent View")
            self.assertEqual(platform_support.cache_dir(), Path.home() / "Library" / "Caches" / "agent-view")
            with patch.dict(os.environ, {"XDG_STATE_HOME": str(self.root), "XDG_CACHE_HOME": str(self.root)}):
                self.assertEqual(platform_support.state_dir(), self.root)
                self.assertEqual(platform_support.cache_dir(), self.root / "agent-view")

    @unittest.skipUnless(sys.platform == "darwin", "requires native macOS codesign")
    def test_native_codesign_smoke(self):
        # A real, small Mach-O executable exercises the native signing command on
        # macOS CI without downloading or executing Claude.
        executable = self.bundle / "Contents" / "MacOS" / "Probe"
        executable.parent.mkdir()
        # Copy bytes and executable permissions without inheriting SIP's
        # restricted filesystem flags from the Apple system binary.
        shutil.copyfile("/bin/echo", executable)
        executable.chmod(0o755)
        # This fixture has a dummy helper with no executable, so remove it before
        # testing codesign's real nested-bundle traversal.
        shutil.rmtree(self.bundle / "Contents" / "Frameworks")
        macos.sign_bundle(self.bundle)
        result = subprocess.run([str(executable), "signed"], capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), "signed")


if __name__ == "__main__":
    unittest.main()
