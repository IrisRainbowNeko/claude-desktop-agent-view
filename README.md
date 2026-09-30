# Agent View

A local dashboard for Claude Code sessions and their subagents. It opens inside
the Claude Desktop Code tab's Browser pane, and it also works in any browser.

![Graph view of a session with three levels of subagents](docs/graph-view.png)

- **Agent map**: shows the main session and its subagents as a tree, based on
  who spawned which subagent. Switch between an indented list and a
  left-to-right graph with the 列表 / 图 toggle (remembered per browser). Each card shows status (running / waiting /
  done / stale), the tool currently running, elapsed time, tool call count,
  output tokens, context length and error count.
- **Transcript**: click a card to see that agent's messages, thinking and tool
  calls with their results. The transcript follows new output live.
- **Rendering**: messages and thinking are rendered as Markdown with LaTeX math
  (KaTeX: `$…$`, `$$…$$`, amsmath environments) and MyST syntax: directives such
  as `{note}` / `{warning}`, `:::` colon fences, roles like `{math}`, targets,
  footnotes. The 渲染 checkbox switches back to plain text. Raw HTML in messages
  is shown as text, never executed.
- **Live updates** are pushed over SSE.

![List view with a subagent's transcript open and a tool-call group expanded](docs/transcript-view.png)

Stdlib Python 3 only, nothing to install. The Markdown renderer is a prebuilt
bundle in `static/vendor/` (markdown-it, the executablebooks MyST plugins and
KaTeX, all MIT). The server inlines it into the page. To rebuild it, run
`cd tools/markdown && npm install && node build.mjs`.

## Data sources

| Source | Provides | Required? |
|---|---|---|
| `~/.claude/projects/**/*.jsonl` and `subagents/agent-*.jsonl` + `.meta.json` | tree structure, history, tokens, tool calls | Yes. Polled once per second. |
| hooks → `hook.py` → `POST /event` | precise state: waiting for permission, turn finished, subagent stopped | No, but recommended |

## Usage

1. Start the server. Pick one of these:
   - In Desktop, open the Browser pane and choose the `agent-view` config
     (from `.claude/launch.json`), or ask Claude to open it. The config runs
     `preview.py`, a small proxy on a port Desktop picks. It forwards to the
     server on 7777 and starts that server first if it is not running, so the
     pane works whether the hooks started the server already or not.
   - Keep it running permanently with `agent-view.service` (see the comments at
     the top of that file), or let the hooks start it (below).
   - Or run `python3 server.py` by hand and open http://127.0.0.1:7777.
2. (Optional) Install the hooks:
   ```bash
   python3 install_hooks.py            # writes ~/.claude/settings.json and backs it up first
   python3 install_hooks.py --dry-run  # preview only
   python3 install_hooks.py --uninstall
   ```
   The hooks run with `async: true`, so they never block Claude. Sessions that
   are already running may need a restart before the hooks take effect.

   With the hooks installed, the server starts itself: on `SessionStart` and
   `UserPromptSubmit`, if nothing is listening on the port, `hook.py` launches
   `server.py` detached from the session (log: `~/.local/state/agent-view.log`).
   Other events never start it; they are dropped silently while it is down. Set
   `AGENT_VIEW_AUTOSTART=0` to turn this off.

   The installer also adds a `SessionStart` hook, `autopreview.py`, that makes
   the preview available in every project. In Desktop sessions it adds an
   `agent-view` entry to the project's `.claude/launch.json`, so you can start
   it from the Browser pane with one click. It creates the file if needed and
   adds it to `.git/info/exclude`. An existing file keeps its other entries.
   A `launch.json` that is committed to git is never touched.

   Desktop only runs a session's hooks after you send a message in it. To cover
   the time before that, the server watches Desktop's session records
   (`~/.config/Claude*/claude-code-sessions`, or `AGENT_VIEW_DESKTOP_SESSIONS`).
   When you create a session in a new folder, the entry appears there within a
   few seconds. Worktree and archived sessions are skipped. For projects that
   only have CLI transcripts, run `python3 autopreview.py --all` once
   (`--all --dry-run` lists them first).

   With `AGENT_VIEW_AUTOPREVIEW=auto` the hook also tells Claude to open the
   preview before its first reply, since only a tool call can open the pane.
   `AGENT_VIEW_AUTOPREVIEW=0` turns the hook off entirely.
   Uninstalling the hooks leaves the `launch.json` entries in place.

## SSH sessions

Claude Desktop sessions that run on a remote host over SSH show up in the same
dashboard with a host tag, and nothing has to be installed on the server. The
server reads Desktop's session records to find each SSH session's host alias
and remote transcript path. It keeps one `ssh <host> python3 ...` connection per
host with sessions active in the last day. That connection streams new
transcript and subagent bytes into `~/.cache/agent-view/remote/<host>/`. It
opens no port on the server, which matters on shared login nodes.

Requirements: `python3` on the server, and ssh that works without prompting
(keys or an agent), since the connection runs with `BatchMode=yes`. Connection
problems show in the header. Hook events are not forwarded, so the status of
remote sessions is inferred from their transcripts. If the ssh connection
fails, Agent View falls back to Desktop's own copy in `~/.claude/projects/ssh-*`,
which has no subagent tree. Set `AGENT_VIEW_REMOTE=0` to turn this off.

Desktop's Browser pane is not available in SSH sessions, so there is no
button to open the dashboard there. Open http://127.0.0.1:7777 in a browser and
tick 跟随 Desktop (follow Desktop): the dashboard then switches to whichever
session you focus in Desktop. For a button inside Desktop, see the optional
patch below.

## Advanced: Agent View sidebar in Desktop SSH sessions (optional)

`desktop-patch/` patches Claude Desktop on Linux itself. It adds a globe
**Agent View** button to the toolbar of SSH sessions, which opens that session's
dashboard in Desktop's right sidebar. The same toggle is in
**View > Toggle Agent View Sidebar** (`Ctrl+Alt+G`), and
`claude-desktop --agent-view` triggers it from a terminal. The pane stays bound
to its session, whatever the 跟随 Desktop setting says. Local sessions keep the
normal Browser pane entry.

This is unofficial and unsupported. Desktop has no extension API, so the patch
edits Desktop's minified code in `app.asar` and three renderer files. It only
works on Desktop builds whose anchors were reviewed; currently that is
**2.7032.0**. On any other version, or if an anchor does not match exactly once,
or if a frontend file's hash differs, it refuses and changes nothing.

```bash
node desktop-patch/patch.mjs status                    # version, supported?, installed?
sudo node desktop-patch/patch.mjs install              # restart Desktop afterwards
sudo node desktop-patch/patch.mjs install --pacman-hook  # also reapply after package upgrades (Arch)
sudo node desktop-patch/patch.mjs restore              # put the original files back
```

Details:

- The default location is `/usr/lib/claude-desktop/resources`. Use
  `--resources DIR` or `CLAUDE_DESKTOP_RESOURCES` for another install.
- The files as they were before the patch are kept in
  `<resources>/agent-view-patch/` for `restore`. The ASAR edit is applied to the
  installed `app.asar`, so other local changes in it are kept.
- The sidebar is served through an isolated `app://agent-view` origin. It only
  proxies Agent View's read-only GET endpoints, and the page gets no Node or
  Desktop APIs (see `desktop-patch/launcher.cjs`, which is copied into the archive).
  When the sidebar is opened, it starts `server.py` from this checkout (or
  `--server PATH`) if nothing is listening on 7777. Moving the checkout means
  running `install` again.
- The pacman hook runs `after-update`. That command reinstalls on a reviewed
  version and leaves an unreviewed one untouched. On other distributions, run
  `install` again after each Desktop upgrade.
- Supporting a new Desktop version means finding the same anchors in its
  bundles and adding an entry to `BUILDS` in `desktop-patch/patch.mjs`.

## Configuration

The server listens on `127.0.0.1` only, since transcripts can contain sensitive
content. Environment variables: `AGENT_VIEW_PORT`, `AGENT_VIEW_HOST`,
`AGENT_VIEW_URL` (the address `hook.py` posts to), `CLAUDE_CONFIG_DIR`.

## Notes

- The jsonl format is not a stable public interface; parsing may need updates
  after Claude Code upgrades. All parsing lives in `Transcript._feed` and
  `render_entry` in `server.py`.
- Without hooks, status is inferred from transcripts. "Waiting for permission"
  can't be detected that way and shows as running.
- Large transcripts only load the last 3 MB / 400 items by default. Click "全部"
  (all) to load everything.
- Tests: `python3 -m unittest discover -s tests` and
  `node --test tests/*.cjs tests/*.mjs`.

## License

MIT, see [LICENSE](LICENSE).
