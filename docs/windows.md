# Windows

Use Python 3.10+ and Git for Windows. The optional Desktop sidebar patch also
needs Node.js 24+. Run these commands in PowerShell from this checkout:

```powershell
python install_hooks.py --dry-run
python install_hooks.py
python server.py
# Open http://127.0.0.1:7777
```

The hooks start a hidden background server on the first session/prompt. You can
close the manually started server after checking it. New Desktop projects get
an `agent-view` Browser configuration using the absolute Python executable.
Tracked launch configurations are preserved, including this repository's
`python3` configuration; adjust that entry locally if your Python installation
only provides `python`, or use the dashboard in a browser.

Windows hooks use Claude Code's [exec-form command hooks](https://code.claude.com/docs/en/hooks#command-hook-fields)
(`command` plus `args`), so paths with spaces and Unicode do not depend on Bash
or PowerShell quoting. Use a current Claude Code/Desktop build with exec-form
hook support. Desktop session records are discovered under
`%APPDATA%\Claude*\claude-code-sessions`. Use `AGENT_VIEW_DESKTOP_SESSIONS` for a
nonstandard data directory. Logs are in `%LOCALAPPDATA%\agent-view`, and remote
transcripts are in its `cache\remote` subdirectory.

SSH sessions require Windows OpenSSH on `PATH` and noninteractive key/agent
authentication. The remote host needs `python3`; remote Windows hosts are not
supported by the existing POSIX SSH collector. Use native Windows Python for
native Windows Desktop; WSL uses Linux paths and is a separate environment.

## Desktop SSH sidebar

The native **Windows x64 Desktop 4.4212.0** bundles have been reviewed. The patch
adds the Agent View globe button, **View > Toggle Agent View Sidebar** and
`Ctrl+Alt+G`, with a sandboxed frame bound to the current SSH session. Ordinary
local sessions retain their Browser preview. Other builds/architectures refuse
before changing any files.

```powershell
.\desktop-patch\install-windows.ps1
.\desktop-patch\install-windows.ps1 -Command status
# Fully quit the official Claude app first (including its tray process).
.\desktop-patch\install-windows.ps1 -Command launch
```

The installer discovers the current user's MSIX package, copies its `app`
directory to `%LOCALAPPDATA%\Agent View\Claude`, and patches the copy. For other
installations, pass `-Source 'C:\path\to\app'` (the directory containing
`claude.exe` and `resources`) or choose a fresh `-Destination`.

Both `app.asar` and `ion-dist.asar` are rebuilt with per-file integrity. Their
header hashes are updated in the copied executable's embedded integrity table.
Editing the executable invalidates its vendor signature, so the copy is marked
unsigned. No certificate is generated or trusted, no service certificate is
replaced, and the original MSIX is untouched. Windows policies that require
vendor-signed apps can prevent this copy from launching. Use the official app
for Cowork, whose service authenticates the vendor signature. This patch targets
the Code tab and its SSH dashboard.

The launcher captures the Python interpreter chosen at install time. If needed,
pass `-Python 'C:\path\to\python.exe'` when installing. Keep the checkout in
place: the patched launcher starts its `server.py`.

To undo the patch, fully quit the copy, then run:

```powershell
.\desktop-patch\install-windows.ps1 -Command restore
python install_hooks.py --uninstall
```

Restore verifies all installed hashes and backup hashes before writing anything;
it restores both archives and the original signed executable byte for byte. If
an updater or another patch changed files, it keeps the backup and refuses.
After an official Desktop update, prepare a new copy in a fresh destination.
The native installer does not register an update hook or modify package ACLs.

## Validation

```powershell
python -m unittest discover -s tests
node --test tests/*.cjs tests/*.mjs
# Optional: validate the real reviewed installation without modifying it.
$env:AGENT_VIEW_WINDOWS_RESOURCES = 'C:\path\to\app\resources'
node --test tests/test_native_patch.mjs
```

The optional test applies every anchor in memory and syntax-checks the patched
main process and renderer bundles. Native GUI behavior still needs a manual
check: open two different SSH sessions, verify each pane stays with its session,
toggle the menu/shortcut, and verify a local Browser preview still works.
