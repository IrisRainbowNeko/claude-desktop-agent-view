# Desktop SSH sidebar patch

For the native Windows build (including its signed MSIX layout), use
[the Windows guide](windows.md). The Linux installer below targets the Linux
repackaging; `native.mjs` handles native double-ASAR bundles separately.

`desktop-patch/` adds an **Agent View** button to SSH sessions in Claude Desktop on
Linux. Clicking it opens that session's dashboard in Desktop's right sidebar. This
page covers what the patch changes, how to install and check it, what happens on
Desktop updates, and how to support a new Desktop version.

It is unofficial. Desktop has no extension API, so the patch edits minified
code. It applies only to Desktop builds whose anchors were reviewed:

| Desktop | Main-process chunk | Renderer files (`ion-dist/assets/v1/`) |
|---|---|---|
| 2.7032.0 | `index.chunk-CxyX_nIQ.js` | `c11959232-5NqmfTEB.js`, `cccc2cf0a-DjnwAvES.js`, `cc70f2bdf-B2j9Ioay.js` |
| 2.31226.1 | `index.chunk-BsJ4Sf2W.js` | `c11959232-CDBo_AYC.js`, `cccc2cf0a-DHtZKTKW.js`, `ca80fca8d-uAxy5Mjj.js` |
| 2.42304.0 | `index.chunk-Z7WxBjyE.js` | `c11959232-Dx4NfIyJ.js`, `cccc2cf0a-B0D--VZp.js`, `c73f45bc3-CFrHin0p.js` |

## What it changes

The patch has four parts, and all of them are needed:

1. **Main process** (one chunk inside `app.asar`):
   - It adds **View > Toggle Agent View Sidebar** (`Ctrl+Alt+G`). The item toggles
     the session's Preview pane, so Desktop's own sidebar does the layout.
   - It serves `app://agent-view/` through `desktop-patch/launcher.cjs`, which is
     copied into the archive as `.vite/build/agentViewLauncher.js`. The launcher
     proxies only the dashboard's read-only GET endpoints to
     `http://127.0.0.1:7777` and strips cookies. Redirects that would leave those
     endpoints are refused.
   - It allows `app://agent-view` in the `frame-src` of the app page's CSP.
   - The launcher also starts `server.py` when nothing is listening on 7777. It
     handles `claude-desktop --agent-view`.
2. **Pane availability** (`c11959232-*.js`): the Preview pane is allowed in SSH
   sessions too.
3. **Pane content** (`cccc2cf0a-*.js`): in an SSH session, the Preview pane holds
   a sandboxed frame,
   `app://agent-view/?desktop_session=<Desktop session id>`. The server
   resolves the Desktop session id to the session's transcript from Desktop's
   session record. So the pane always shows the session it belongs to, whatever
   the dashboard's 跟随 Desktop setting is. The embedded page is served with a
   strict CSP: no external requests, and it can only be framed by
   `app://localhost`.
4. **Toolbar** (`cc70f2bdf-*.js` / `ca80fca8d-*.js` / `c73f45bc3-*.js`): the
   Preview button is shown in SSH sessions, with a globe icon and the label
   "Agent View".

Local sessions are unaffected. They keep the normal Browser pane.

## Install, check, restore

```bash
node desktop-patch/patch.mjs status
sudo node desktop-patch/patch.mjs install [--server /path/to/server.py] [--pacman-hook]
sudo node desktop-patch/patch.mjs restore
```

Quit Desktop fully before `install` or `restore`, using the app's Quit command.
Closing the window may only hide it in the tray.

- `install` checks everything before it writes anything:
  - the Desktop version is listed in `BUILDS`;
  - each anchor matches exactly once;
  - each renderer file has the reviewed sha256;
  - no other installer has already added an Agent View patch.

  It saves the files as they were to `<resources>/agent-view-patch/`. Running it
  again rebuilds from that saved copy, so the patch is never applied twice.
- The ASAR edit is applied to the installed `app.asar`. Other local changes in
  other chunks are kept.
- `restore` puts back the saved files. It only does this for files that still
  hold what `install` wrote.
- `--server` sets the `server.py` path written into the launcher. The default is
  the one in this checkout. If you move the checkout, run `install` again.
  `AGENT_VIEW_SERVER` and `AGENT_VIEW_PYTHON` in Desktop's environment override
  the path and the interpreter.

If `status` reports "Already contains an Agent View patch from another
installer", a different tool owns the patch on this Desktop. That can be a
combined patch built from these anchors together with other changes. Keep using
that tool. Do not install a second update hook for the same Desktop.

### After you install

1. Check the server: `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:7777/api/hooklog`
   should print `200`.
2. In an SSH session that has subagents, the globe **Agent View** button is in
   the top right. Clicking it opens the sidebar with the main session and its
   subagent tree. Clicking it again closes the sidebar.
3. Switch to another SSH session and open the sidebar there. It shows that
   session.
4. A local session still has its normal Browser pane entry.

The launcher logs to `~/.local/state/agent-view-launcher.log`. The SSH subagent
tree comes from the transcript mirror described under "SSH sessions" in the
README. The patch does not provide it.

| Symptom | Check first |
|---|---|
| No globe button in SSH sessions | `status`: frontend files replaced by an update, or the patch is not installed |
| Sidebar is blank or shows an error | server on 7777, then the launcher log, then whether the CSP edit is present (in `status`) |
| Sidebar shows the wrong session | `?desktop_session=` resolution in `server.py` (`desktop_transcript_id`) |
| Main session is shown but no subagents | remote mirror: ssh in `BatchMode`, `python3` on the host, the error shown in the header |

## Desktop updates

A package upgrade replaces `app.asar` and the renderer files, which removes the
patch. `after-update` handles this:

- if the new version is in `BUILDS`, it installs the patch again;
- otherwise it leaves Desktop untouched and drops the stale backup.

It always exits 0, so it never fails the package transaction. Run `status`
afterwards to see what happened. `install --pacman-hook` registers it as a
pacman hook (`/etc/pacman.d/hooks/agent-view-desktop.hook`). On other
distributions, run `install` again after each upgrade.

## Supporting a new Desktop version

The edits are the same in every version. Only the minified names and file names
change, so supporting a new version means finding each anchor again. Work on
copies:

```bash
mkdir -p /tmp/desk && cd /tmp/desk
cp /usr/lib/claude-desktop/resources/ion-dist/assets/v1/*.js .
node --input-type=module -e '
import { loadAsar, readFile } from "<repo>/desktop-patch/asar.mjs";
import fs from "node:fs";
const a = loadAsar("/usr/lib/claude-desktop/resources/app.asar");
console.log(JSON.parse(readFile(a, "package.json")).version);
for (const [n, e] of Object.entries(a.header.files[".vite"].files.build.files))
  if (n.endsWith(".js") && e.size) fs.writeFileSync(n, readFile(a, ".vite/build/" + n));'
```

1. **Main chunk:** the `.vite/build/index.chunk-*.js` that contains
   `defaultMessage:"View",id:"LCWUQ/4Fu6"`. `mainChanges()` takes four names
   from it:
   - `menu`: the View menu builder, the text right before
     `({label:X().formatMessage({defaultMessage:"View"`, for example `QNi=e=>`.
   - `toggle` and `panes`: from an existing menu item such as
     `AQ(n,(e=>e.dispatchToggleSessionPane(k_.SideChat)),null)`. Here `toggle`
     is `AQ` and `panes` is `k_`.
   - `csp`: the expression after `csp:` in the local `app://` protocol
     handler, the one next to `if(t.hostname!=="localhost")return new
     Response(null,{status:404})`.

   The `electron:a` and `logger:P` names in the inserted menu item must be the
   Electron module and logger in scope at that point. Check this when the View
   menu moves.
2. **SSH test:** the shared renderer chunk `c5da7eb42-*.js` defines a function
   like `function BN(e){return LN(e,FN).remoteKind==="ssh"}`. Find the name
   it is exported as (`export{… BN as H …}`). The other renderer files call it
   through their own import alias of that export.
3. **Pane availability:** in `c11959232-*.js`, find the variable that decides
   whether the Preview pane is shown. Add `agentViewSsh=<alias>(<session>)`
   next to the session lookup. Then OR `<session>?.type==="local"&&agentViewSsh`
   into that condition.
4. **Pane content:** in `cccc2cf0a-*.js`, find
   `if(e==="preview")return <session>?.type==="remote"?`. Add the
   `agentViewSsh` lookup after `<session>=…(u)`, then write `paneChange()` with
   that file's JSX factory and pane component. Copy the component's props from
   the existing preview branch right after the anchor.
5. **Toolbar:** this is the file whose function shows
   `icon:e==="preview"&&…?"Globe"`. Import the SSH test from `c5da7eb42-*.js`,
   OR `agentViewSsh` into the button's visibility, icon and label. Then append
   it to the dependency array of the `useMemo` that builds the buttons.
6. Add a `BUILDS` entry with the sha256 of each unmodified renderer file
   (`sha256sum`). Run `node --test tests/*.cjs tests/*.mjs`. Install on a copy
   of the resources with `--resources DIR`, then on the real install. Go
   through the checks in "After you install".

Take care with these:

- Every anchor must be unique. Extend an anchor with neighbouring text rather
  than relaxing the check.
- Check that a changed module has no matching `.jsc` cache that Desktop loads
  instead.
- `writeAsar` keeps `unpacked` entries and native binaries in
  `app.asar.unpacked` as they are. Do not repack the archive with other tools.
- Never install a patch built for one version on another version.
