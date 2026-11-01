#!/usr/bin/env python3
"""Prepare and ad-hoc sign a separate Claude.app with the Agent View SSH sidebar.

Only publish a staged application after both ASAR integrity and codesign verify.
The vendor-signed source application is never modified.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import struct
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
ARCHIVES = ("app.asar", "ion-dist.asar")


def header_hash(file):
    with open(file, "rb") as stream:
        prefix = stream.read(16)
        if len(prefix) != 16:
            raise ValueError(f"Truncated ASAR: {file}")
        tag, size, payload, length = struct.unpack("<4I", prefix)
        if tag != 4 or length > size - 8 or payload != size - 4:
            raise ValueError(f"Invalid ASAR header: {file}")
        header = stream.read(length)
        if len(header) != length:
            raise ValueError(f"Truncated ASAR header: {file}")
        json.loads(header)
        return hashlib.sha256(header).hexdigest()


def archive_hashes(bundle):
    return {name: header_hash(bundle / "Contents" / "Resources" / name) for name in ARCHIVES}


def update_integrity(bundle, before, after, python=None):
    """Validate every affected plist before writing, including Helper frameworks."""
    edits = {}
    seen = set()
    root = bundle.resolve()
    main = root / "Contents" / "Info.plist"
    main_found = False
    for candidate in root.rglob("Info.plist"):
        file = candidate.resolve()
        if not file.is_relative_to(root):
            raise ValueError(f"Info.plist escapes application bundle: {candidate}")
        if file in seen:
            continue
        seen.add(file)
        original = file.read_bytes()
        data = plistlib.loads(original)
        integrity = data.get("ElectronAsarIntegrity")
        if not isinstance(integrity, dict):
            continue
        for name in ARCHIVES:
            key = "Resources/" + name
            entry = integrity.get(key)
            if not isinstance(entry, dict) or entry.get("algorithm") != "SHA256" or entry.get("hash") != before[name]:
                raise ValueError(f"Unreviewed ASAR integrity in {file}: {key}")
            entry["hash"] = after[name]
        if file == main:
            main_found = True
            if python:
                data.setdefault("LSEnvironment", {})["AGENT_VIEW_PYTHON"] = str(python)
                data["CFBundleDisplayName"] = "Claude Agent View"
        fmt = plistlib.FMT_BINARY if original.startswith(b"bplist") else plistlib.FMT_XML
        edits[file] = plistlib.dumps(data, fmt=fmt, sort_keys=False)
    if not main_found:
        raise ValueError("Main application has no reviewed ElectronAsarIntegrity table.")
    for file, contents in edits.items():
        file.write_bytes(contents)
    return len(edits)


def run(*args):
    return subprocess.run([str(arg) for arg in args], check=True)


def sign_bundle(bundle):
    # Retain entitlements and hardened-runtime flags on nested Electron helpers.
    # Keep the system's validation enabled; never remove quarantine recursively.
    run("/usr/bin/codesign", "--force", "--deep", "--sign", "-", "--timestamp=none",
        "--preserve-metadata=entitlements,flags,runtime", bundle)
    run("/usr/bin/codesign", "--verify", "--deep", "--strict", bundle)


def patch_copy(source, destination, command, node, server):
    source, destination = source.resolve(), destination.absolute()
    if source.suffix != ".app" or destination.suffix != ".app":
        raise ValueError("Source and destination must be .app bundles.")
    if not (source / "Contents" / "Resources" / "app.asar").is_file():
        raise ValueError(f"No Claude Desktop app.asar in {source}")
    resolved_destination = destination.resolve()
    if command == "install" and (destination.exists() or destination.is_symlink()):
        raise ValueError("Destination exists. Choose a fresh destination.")
    if command == "restore" and source != resolved_destination:
        raise ValueError("Restore must target the same application copy.")
    if command == "restore" and not (source / "Contents" / "Resources" / "agent-view-native-patch" / "state.json").is_file():
        raise ValueError("This copy has no Agent View backup to restore.")
    if destination.is_symlink() or str(resolved_destination).startswith("/Applications/"):
        raise ValueError("Choose an application copy outside /Applications (default: ~/Applications).")
    if command == "install" and (resolved_destination == source or resolved_destination.is_relative_to(source)):
        raise ValueError("Destination must be outside the source application.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".agent-view-stage-", dir=destination.parent) as temp:
        stage = Path(temp) / destination.name
        run("/usr/bin/ditto", "--rsrc", "--extattr", source, stage)
        before = archive_hashes(stage)
        run(node, HERE / "native.mjs", command, "--platform", "darwin",
            "--resources", stage / "Contents" / "Resources", "--server", server)
        after = archive_hashes(stage)
        count = update_integrity(stage, before, after, python=Path(sys.executable).resolve())
        sign_bundle(stage)
        if archive_hashes(stage) != after:
            raise ValueError("ASAR archives changed during signing.")
        # Restore works on a staging copy as well; a signing failure leaves the
        # currently installed copy and its backups unchanged.
        previous = Path(temp) / "previous.app"
        if command == "restore":
            destination.rename(previous)
        try:
            stage.rename(destination)
        except BaseException:
            if previous.exists():
                previous.rename(destination)
            raise
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("install", "status", "restore"), nargs="?", default="install")
    parser.add_argument("--source", type=Path, default=Path("/Applications/Claude.app"))
    parser.add_argument("--destination", type=Path, default=Path.home() / "Applications" / "Claude Agent View.app")
    parser.add_argument("--server", type=Path, default=HERE.parent / "server.py")
    parser.add_argument("--node", default=shutil.which("node"))
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("Run this installer on macOS. The archive/plist tests can run on any OS.")
    if not args.node:
        parser.error("Install Node.js 24+ or pass --node /absolute/path/to/node.")
    if args.command == "status":
        run(args.node, HERE / "native.mjs", "status", "--platform", "darwin",
            "--resources", args.destination / "Contents" / "Resources")
        return
    source = args.source if args.command == "install" else args.destination
    count = patch_copy(source, args.destination, args.command, args.node, args.server.resolve())
    print(f"{args.command}: verified both ASAR archives, {count} integrity tables and the ad-hoc signature.")
    print(f"Quit other Claude instances before opening {args.destination}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as err:
        sys.exit(str(err))
