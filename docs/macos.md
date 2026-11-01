# macOS

Use Python 3.10+ (for example Homebrew Python). The optional Desktop SSH sidebar
patch also needs Node.js 24+ and Apple's `codesign` and `ditto` tools. In Terminal,
from this checkout:

```bash
python3 install_hooks.py --dry-run
python3 install_hooks.py
python3 server.py
# Open http://127.0.0.1:7777
```

The hooks automatically start a detached server at the first session/prompt,
so a permanent Terminal window is optional. New Desktop projects receive an
`agent-view` Browser configuration with the absolute Python interpreter. A
tracked launch configuration is preserved. Hook removal is
`python3 install_hooks.py --uninstall`; existing project preview entries remain.

Desktop's session records are discovered in
`~/Library/Application Support/Claude*/claude-code-sessions`; override this with
`AGENT_VIEW_DESKTOP_SESSIONS` if needed. Logs are in
`~/Library/Logs/Agent View`, and remote transcripts are in
`~/Library/Caches/agent-view/remote`. `XDG_STATE_HOME` and `XDG_CACHE_HOME` override
these defaults. SSH mirroring uses the system `ssh`, noninteractive key/agent
authentication and `python3` on the remote POSIX host.

## Desktop SSH sidebar

The native **macOS universal Desktop 4.4212.0** bundles have been reviewed (the
same application package contains Intel and Apple Silicon builds). Unknown main
processes, renderers, dependencies, or ASAR integrity records refuse the patch.

```bash
python3 desktop-patch/install-macos.py
python3 desktop-patch/install-macos.py status
# Fully quit the official Claude app first, then:
open "$HOME/Applications/Claude Agent View.app"
```

The source defaults to `/Applications/Claude.app`. Pass `--source` for another
installation or `--destination` for a fresh target outside `/Applications`.
The installer uses `ditto` to copy the application to a staging directory,
patches both `app.asar` and `ion-dist.asar`, updates the `ElectronAsarIntegrity`
entries in the main app and every Helper/framework `Info.plist`, then ad-hoc
signs the copy, preserving entitlements and hardened-runtime metadata. It runs
`codesign --verify --deep --strict` before publishing the finished copy as
`~/Applications/Claude Agent View.app`. Signing failures leave the original app
and an existing destination untouched.

The copy has an ad-hoc signature, not Anthropic's notarized signature. Managed
policies or Gatekeeper can prevent launching it, and macOS may ask again for
application permissions. The installer does not remove quarantine, install a
certificate, or disable integrity validation. Retain the official app for
features that require the vendor's signing identity. Both apps have the same
bundle identifier and session data; fully quit one before starting the other.

In an SSH Code session, click the globe **Agent View** button, choose
**View > Toggle Agent View Sidebar**, or press `Cmd+Option+G`. The frame is bound
to that session's ID and uses the same isolated read-only proxy as the Linux
patch. Ordinary local Browser previews retain their existing behavior.

The copied app's `LSEnvironment` captures the installer's Python interpreter, so
launching from Finder can start the server without relying on Finder's `PATH`.
Keep this checkout in place. To undo the sidebar patch, fully quit the copy and
run:

```bash
python3 desktop-patch/install-macos.py restore
```

Restore works in another staging copy, verifies the saved ASAR backups, updates
all integrity records and signs again before replacing the patched copy. The
restored copy remains ad-hoc signed; the vendor-signed source app is untouched.
If another updater changed the patched files, recovery refuses and retains the
backup. After an official Desktop update, use a fresh destination for a newly
reviewed build. No system package/update hook is registered.

## Validation

```bash
python3 -m unittest discover -s tests
node --test tests/*.cjs tests/*.mjs
# Optional: validate real reviewed bundles without modifying the app.
AGENT_VIEW_MACOS_RESOURCES=/Applications/Claude.app/Contents/Resources \
  node --test tests/test_native_patch.mjs
```

The bundle test applies every anchor in memory and syntax-checks the patched
main process and renderers. The Python suite tests XML/binary plist integrity,
staged publication, rollback on signing failure and, on macOS, real `codesign`
with a small test application. Full Claude GUI validation still requires a Mac:
open two SSH sessions and verify that each pane follows its own session, check
the button/menu/shortcut, then check an ordinary local Browser preview.

The reviewed universal ZIP is published by Anthropic at
[Claude 4.4212.0](https://downloads.claude.ai/releases/darwin/universal/4.4212.0/Claude-cbfe3c56d49c49332ad5a9588303c9dbc70a5e7f.zip),
SHA256 `e1e144e20d98e793135f584eba92e3d8fa30a8151f6df40bf10846ae407de5fb`.
