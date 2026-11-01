#!/usr/bin/env node
// Optional, unofficial: add an Agent View sidebar to Claude Desktop (Linux) SSH sessions.
//
//   sudo node desktop-patch/patch.mjs status|install|restore|after-update
//        [--resources DIR] [--server PATH] [--pacman-hook]
//
// Desktop has no extension API, so this edits minified code by exact text anchors. Every
// anchor must match exactly once, the Desktop version must be one listed in BUILDS, and the
// frontend files must have the reviewed hashes; otherwise nothing is written. The ASAR edit is
// applied to the app.asar that is installed, so other local patches in it are kept.
// The pre-patch files are kept in <resources>/agent-view-patch/ for `restore`.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { readAsar, writeAsar, readFile, sha256 } from "./asar.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.dirname(HERE);
const LAUNCHER = path.join(HERE, "launcher.cjs");
const LAUNCHER_NAME = ".vite/build/agentViewLauncher.js";
const MARKER = "agentViewLauncher";
const FRONTEND = "ion-dist/assets/v1";
const HOOK = "/etc/pacman.d/hooks/agent-view-desktop.hook";
const SERVER_LINE = 'const SERVER = process.env.AGENT_VIEW_SERVER || path.join(__dirname, "..", "server.py");';

// Reviewed builds. The edits are the same in every version, only the minified names differ.
// main: [old, new] replacements in the main-process chunk inside app.asar.
// frontend: renderer files next to app.asar, with the sha256 of the unmodified file.
// To support a new version, find each anchor in its bundles (see docs/desktop-patch.md).
const VIEW_MENU = '({label:X().formatMessage({defaultMessage:"View",id:"LCWUQ/4Fu6"}),submenu:[';
const PROTOCOL = 'let t=new URL(e.url);if(t.hostname!=="localhost")return new Response(null,{status:404});let i=e.headers.get("Origin")';

// menu: the View menu builder ("YHr=()=>"); toggle/panes: the functions that toggle the
// session pane and the pane enum; csp: the expression building the app page's CSP.
function mainChanges({ menu, toggle, panes, csp }) {
  return [
    // View menu: "Toggle Agent View Sidebar" (Ctrl+Alt+G), opening the session's side pane.
    [menu + VIEW_MENU, menu + VIEW_MENU + `require("./agentViewLauncher.js").menuItem({electron:a,toggleSidebar:event=>${toggle}(event,dispatcher=>dispatcher.dispatchToggleSessionPane(${panes}.Preview)),logger:P}),{type:"separator"},`],
    // Allow the app://agent-view frame in the app page's CSP.
    [`csp:${csp}`, `csp:${csp}.replace(/frame-src([^;]*)/,directive=>directive+" app://agent-view")`],
    // Serve app://agent-view/ as a read-only proxy to the local Agent View server.
    [PROTOCOL, 'let t=new URL(e.url);if(t.hostname==="agent-view")return require("./agentViewLauncher.js").proxyRequest(e,a);' + PROTOCOL.slice("let t=new URL(e.url);".length)],
  ];
}

// In SSH sessions the Preview pane holds the Agent View frame for that session. `session` is
// the pane's session variable, `h` the JSX factory, `pane` the pane component and its props.
function paneChange(session, h, pane) {
  const before = `if(e==="preview")return ${session}?.type==="remote"?`;
  const frame = `${h}("iframe",{key:${session}.id,title:"Agent View",src:"app://agent-view/?desktop_session="+encodeURIComponent(${session}.id),sandbox:"allow-scripts allow-same-origin",referrerPolicy:"no-referrer",style:{width:"100%",height:"100%",minHeight:0,flex:1,border:0,background:"#fff"}})`;
  return [before, `if(e==="preview"&&${session}?.type==="local"&&agentViewSsh)return ${h}(${pane},title:"Agent View",children:${frame}});${before}`];
}

export const BUILDS = {
  "2.7032.0": {
    chunk: ".vite/build/index.chunk-CxyX_nIQ.js",
    main: mainChanges({ menu: "YHr=()=>", toggle: "TQ", panes: "qv", csp: "Y4t(iu(t),c,l)" }),
    frontend: {
      // Session pane: allow the Preview pane in SSH sessions.
      "c11959232-5NqmfTEB.js": ["20538c99cb343ef07d569bcd5b0b6c02baef57255c624d39fa5af02a9977e880", [
        ["te=ia(u),ne=la(n)", "te=ia(u),agentViewSsh=Xi(u),ne=la(n)"],
        ["M=te||!!g||", 'M=te||u?.type==="local"&&agentViewSsh||!!g||'],
      ]],
      "cccc2cf0a-DjnwAvES.js": ["2cdd905412b485ec85cde156f924647aab4370e48af8eead80440951d21ca103", [
        ["C=Ta(u),w=Po(e)", "C=Ta(u),agentViewSsh=pa(C),w=Po(e)"],
        paneChange("C", "g", 'Wo,{style:{height:"100%"},leadingClearance:d,center:m,tileId:t,onClose:ue,hideCloseButton:y,onEscapeClose:b?void 0:fe'),
      ]],
      // Toolbar: the globe "Agent View" button in SSH sessions.
      "cc70f2bdf-B2j9Ioay.js": ["0f68ab60d25e1ff6fbe64521b0157a72ba1081dcb31426baf42b0aba7820130e", [
        ["function ef({owner:e,via:t})", 'import{P as agentViewIsSsh}from"./c5da7eb42-DojRZ_Aj.js";function ef({owner:e,via:t})'],
        ["g=or(r,Zd),_=ur(e,Zd)", 'g=or(r,Zd),agentViewSshHook=agentViewIsSsh(r),agentViewSsh=r?.type==="local"&&agentViewSshHook,_=ur(e,Zd)'],
        ["x=g||!!o||", "x=g||agentViewSsh||!!o||"],
        ['icon:e==="preview"&&b?"Globe":Mi[e]', 'icon:e==="preview"&&(b||agentViewSsh)?"Globe":Mi[e]'],
        ['r=t("preview",!!x,b?n.formatMessage', 'r=t("preview",!!x,agentViewSsh?"Agent View":b?n.formatMessage'],
        ["[e,i,n,l,u,x,_,y,m,b,S,C,d,j,M,h]", "[e,i,n,l,u,x,_,y,m,b,S,C,d,j,M,h,agentViewSsh]"],
      ]],
    },
  },
  "2.31226.1": {
    chunk: ".vite/build/index.chunk-BsJ4Sf2W.js",
    main: mainChanges({ menu: "Zbi=()=>", toggle: "r$", panes: "Jg", csp: "xun(Ul(t),c,l)" }),
    frontend: {
      "c11959232-CDBo_AYC.js": ["bf5d1058293a7a83a8853831a9a8be1e6adba88805f4cb1099225ca89c36113b", [
        ["ee=za(c),O=ro(t)", "ee=za(c),agentViewSsh=qa(c),O=ro(t)"],
        ["j=ee||!!m||", 'j=ee||c?.type==="local"&&agentViewSsh||!!m||'],
      ]],
      "cccc2cf0a-DHtZKTKW.js": ["0f13bcdc39572e06d535ff0234b319ad15f63d92445a570c656c56bc8b1a41cb", [
        ["E=Ss(u),D=", "E=Ss(u),agentViewSsh=vs(E),D="],
        paneChange("E", "v", 'Lm,{style:{height:"100%"},leadingClearance:f,center:p,tileId:t,onClose:_e,hideCloseButton:h,onEscapeClose:g?void 0:be'),
      ]],
      "ca80fca8d-uAxy5Mjj.js": ["61c76a540331b8cac4946a37cb8ab3ade4ef471e96d8b0829e7901949c931414", [
        ["function u4({owner:e,via:t})", 'import{V as agentViewIsSsh}from"./c5da7eb42-BqFpOdHJ.js";function u4({owner:e,via:t})'],
        ["m=zp(r,s4),h=gm(e,s4)", 'm=zp(r,s4),agentViewSshHook=agentViewIsSsh(r),agentViewSsh=r?.type==="local"&&agentViewSshHook,h=gm(e,s4)'],
        ["y=m||!!o||", "y=m||agentViewSsh||!!o||"],
        ['icon:e==="preview"&&_?"Globe":Ag[e]', 'icon:e==="preview"&&(_||agentViewSsh)?"Globe":Ag[e]'],
        ['r=t("preview",!!y,_?n.formatMessage', 'r=t("preview",!!y,agentViewSsh?"Agent View":_?n.formatMessage'],
        ["[e,i,n,l,u,y,h,g,p,_,b,x,d,j,N,P]", "[e,i,n,l,u,y,h,g,p,_,b,x,d,j,N,P,agentViewSsh]"],
      ]],
    },
  },
  "2.42304.0": {
    chunk: ".vite/build/index.chunk-Z7WxBjyE.js",
    main: mainChanges({ menu: "QNi=e=>", toggle: "AQ", panes: "k_", csp: "Dmn(Yl(t),c,l)" }),
    frontend: {
      "c11959232-Dx4NfIyJ.js": ["ae0db7e8daafb631da8155ce106127ade06a3c461c383f473dafcd4532cd63cd", [
        ["te=ao(s),O=jo(n)", "te=ao(s),agentViewSsh=io(s),O=jo(n)"],
        ["j=te||A&&ie||", 'j=te||s?.type==="local"&&agentViewSsh||A&&ie||'],
      ]],
      "cccc2cf0a-B0D--VZp.js": ["4f75e5db4ff595b4fc4f22a9092dfa245b4af29f964bb207276b868a606964cb", [
        ["N=sl(u),P=", "N=sl(u),agentViewSsh=Fc(N),P="],
        paneChange("N", "g", 'Ig,{style:{height:"100%"},leadingClearance:f,center:p,tileId:t,onClose:Te,hideCloseButton:_,onEscapeClose:v?void 0:Ae'),
      ]],
      "c73f45bc3-CFrHin0p.js": ["c328ce5a89455c1901061fa581e32f471507a474b2d0880d4dc3a626048253c9", [
        ["function Rh({owner:e,via:t})", 'import{H as agentViewIsSsh}from"./c5da7eb42-B8cTG8SI.js";function Rh({owner:e,via:t})'],
        ["h=ga(r,Fh),g=qs(r,Fh)", 'h=ga(r,Fh),agentViewSshHook=agentViewIsSsh(r),agentViewSsh=r?.type==="local"&&agentViewSshHook,g=qs(r,Fh)'],
        ["k=h||T&&O||", "k=h||agentViewSsh||T&&O||"],
        ['icon:e==="preview"&&C?"Globe":Io[e]', 'icon:e==="preview"&&(C||agentViewSsh)?"Globe":Io[e]'],
        ['r=t("preview",!!k,C?n.formatMessage', 'r=t("preview",!!k,agentViewSsh?"Agent View":C?n.formatMessage'],
        ["[e,i,n,l,d,k,b,x,S,m,C,A,j,f,V,ee,te]", "[e,i,n,l,d,k,b,x,S,m,C,A,j,f,V,ee,te,agentViewSsh]"],
      ]],
    },
  },
};

class Refusal extends Error {
  constructor(message, code = 2) { super(message); this.code = code; }
}

export function replaceEach(source, changes, label) {
  for (const [before, after] of changes) {
    const at = source.indexOf(before);
    if (at < 0 || source.indexOf(before, at + 1) >= 0) {
      throw new Refusal(`${label}: anchor not found exactly once: ${before.slice(0, 60)}`);
    }
    source = source.slice(0, at) + after + source.slice(at + before.length);
  }
  return source;
}

export function launcherSource(server, text = fs.readFileSync(LAUNCHER, "utf8")) {
  if (text.split(SERVER_LINE).length !== 2) throw new Refusal("launcher.cjs: SERVER line not found");
  return text.replace(SERVER_LINE, `const SERVER = process.env.AGENT_VIEW_SERVER || ${JSON.stringify(server)};`);
}

function paths(resources) {
  const state = path.join(resources, "agent-view-patch");
  return { asar: path.join(resources, "app.asar"), frontend: path.join(resources, FRONTEND), state,
    stateFile: path.join(state, "state.json"), backup: path.join(state, "app.asar"),
    backupFrontend: path.join(state, "frontend") };
}

function writeAtomic(file, buf) {
  const tmp = `${file}.agent-view-tmp-${process.pid}`;
  fs.writeFileSync(tmp, buf, { mode: 0o644 });
  fs.renameSync(tmp, file);
}

function loadState(p) {
  try {
    return JSON.parse(fs.readFileSync(p.stateFile, "utf8"));
  } catch {
    return null;
  }
}

// Everything install needs, checked before anything is written.
export function inspect({ resources, builds = BUILDS }) {
  const p = paths(resources);
  if (!fs.existsSync(p.asar)) throw new Refusal(`No app.asar in ${resources}; pass --resources`, 1);
  const current = fs.readFileSync(p.asar);
  let archive = readAsar(current);
  const version = JSON.parse(readFile(archive, "package.json")).version;
  const state = loadState(p);
  const ours = state && sha256(current) === state.installed;
  const out = { p, version, state, ours, build: builds[version] };
  if (ours) archive = readAsar(fs.readFileSync(p.backup));
  out.base = archive;
  if (!out.build) return out;
  const chunk = readFile(archive, out.build.chunk);
  out.foreign = !chunk || chunk.includes(MARKER) || !!readFile(archive, LAUNCHER_NAME);
  out.frontend = {};
  for (const [file, [original]] of Object.entries(out.build.frontend)) {
    const f = path.join(p.frontend, file);
    const digest = fs.existsSync(f) ? sha256(fs.readFileSync(f)) : "missing";
    const saved = state?.frontend?.[file];
    out.frontend[file] = digest === original ? "original" : digest === saved ? "ours" : digest;
  }
  return out;
}

export function status(opts) {
  const s = inspect(opts);
  const lines = [`Claude Desktop ${s.version} in ${opts.resources}`];
  if (!s.build) lines.push("This version has not been reviewed for the Agent View patch.");
  else if (s.ours && Object.values(s.frontend).every((v) => v === "ours")) lines.push("Agent View sidebar patch: installed.");
  else if (s.ours) lines.push("Agent View sidebar patch: installed, but the frontend files changed since.");
  else if (s.foreign) lines.push("Already contains an Agent View patch from another installer; leaving it alone.");
  else if (Object.values(s.frontend).every((v) => v === "original")) lines.push("Agent View sidebar patch: not installed (supported).");
  else lines.push("Frontend files do not match the reviewed build: " + JSON.stringify(s.frontend));
  return { s, text: lines.join("\n") };
}

export function install({ resources, server = path.join(REPO, "server.py"), builds = BUILDS,
                          launcher } = {}) {
  const s = inspect({ resources, builds });
  if (!s.build) throw new Refusal(`Claude Desktop ${s.version} has not been reviewed; nothing changed.`);
  if (s.foreign) throw new Refusal("app.asar already has an Agent View patch that this tool did not install; nothing changed.");
  const originals = {};
  const patched = {};
  for (const [file, [hash, changes]] of Object.entries(s.build.frontend)) {
    const where = s.frontend[file] === "original" ? path.join(s.p.frontend, file)
      : s.frontend[file] === "ours" ? path.join(s.p.backupFrontend, file) : null;
    if (!where) throw new Refusal(`Unknown frontend file ${file} (${s.frontend[file]}); nothing changed.`);
    originals[file] = fs.readFileSync(where);
    if (sha256(originals[file]) !== hash) throw new Refusal(`Backup of ${file} is damaged; nothing changed.`, 1);
    patched[file] = Buffer.from(replaceEach(originals[file].toString("utf8"), changes, file));
  }
  const chunk = replaceEach(readFile(s.base, s.build.chunk).toString("utf8"), s.build.main, s.build.chunk);
  const asar = writeAsar(s.base, {
    [s.build.chunk]: Buffer.from(chunk),
    [LAUNCHER_NAME]: Buffer.from(launcherSource(server, launcher)),
  });

  const p = s.p;
  fs.mkdirSync(p.backupFrontend, { recursive: true, mode: 0o755 });
  if (!s.ours) writeAtomic(p.backup, fs.readFileSync(p.asar));
  for (const [file, buf] of Object.entries(originals)) writeAtomic(path.join(p.backupFrontend, file), buf);
  const state = { version: s.version, server, installed: sha256(asar),
    frontend: Object.fromEntries(Object.entries(patched).map(([f, b]) => [f, sha256(b)])) };
  writeAtomic(p.stateFile, JSON.stringify(state, null, 2) + "\n");
  writeAtomic(p.asar, asar);
  for (const [file, buf] of Object.entries(patched)) writeAtomic(path.join(p.frontend, file), buf);
  return `Installed the Agent View sidebar patch for Claude Desktop ${s.version} (server: ${server}). Restart Claude Desktop.`;
}

export function restore({ resources }) {
  const p = paths(resources);
  const state = loadState(p);
  if (!state) return "Nothing to restore: this tool has not patched this Desktop.";
  const current = fs.existsSync(p.asar) ? sha256(fs.readFileSync(p.asar)) : "missing";
  if (current === state.installed) writeAtomic(p.asar, fs.readFileSync(p.backup));
  for (const [file, hash] of Object.entries(state.frontend)) {
    const f = path.join(p.frontend, file);
    if (fs.existsSync(f) && sha256(fs.readFileSync(f)) === hash) {
      writeAtomic(f, fs.readFileSync(path.join(p.backupFrontend, file)));
    }
  }
  fs.rmSync(p.state, { recursive: true, force: true });
  let msg = current === state.installed ? "Restored the original Claude Desktop files."
    : "app.asar was replaced since the patch (e.g. by an update); left it as it is and removed the backup.";
  if (resources === defaultResources() && fs.existsSync(HOOK)) {
    fs.rmSync(HOOK);
    msg += `\nRemoved ${HOOK}.`;
  }
  return msg + " Restart Claude Desktop.";
}

// Package manager hook: reinstall on a reviewed version, otherwise leave Desktop untouched.
export function afterUpdate(opts) {
  const p = paths(opts.resources);
  const state = loadState(p);
  if (state && fs.existsSync(p.asar) && sha256(fs.readFileSync(p.asar)) !== state.installed) {
    fs.rmSync(p.state, { recursive: true, force: true });  // the update replaced our files
  }
  const s = inspect(opts);
  if (!s.build) return `Agent View: Claude Desktop ${s.version} has not been reviewed; left unpatched.`;
  if (s.ours) return "Agent View: sidebar patch still installed.";
  return install(opts);
}

function installHook() {
  const node = process.execPath;
  const script = fileURLToPath(import.meta.url);
  fs.mkdirSync(path.dirname(HOOK), { recursive: true });
  fs.writeFileSync(HOOK, `[Trigger]
Operation = Install
Operation = Upgrade
Type = Package
Target = claude-desktop

[Action]
Description = Reapplying the Agent View sidebar patch (if this Desktop version is reviewed)...
When = PostTransaction
Exec = "${node}" "${script}" after-update
`);
  return `Installed ${HOOK}.`;
}

function defaultResources() {
  return process.env.CLAUDE_DESKTOP_RESOURCES || "/usr/lib/claude-desktop/resources";
}

function main(argv) {
  const opt = (name) => {
    const i = argv.indexOf(name);
    return i >= 0 ? argv[i + 1] : undefined;
  };
  const cmd = argv.find((a, i) => !a.startsWith("--") && !["--resources", "--server"].includes(argv[i - 1])) || "status";
  const opts = { resources: path.resolve(opt("--resources") || defaultResources()) };
  if (opt("--server")) opts.server = path.resolve(opt("--server"));
  try {
    if (cmd === "status") {
      console.log(status(opts).text);
    } else if (cmd === "install") {
      console.log(install(opts));
      if (argv.includes("--pacman-hook")) console.log(installHook());
    } else if (cmd === "restore") {
      console.log(restore(opts));
    } else if (cmd === "after-update") {
      try {
        console.log(afterUpdate(opts));
      } catch (err) {
        console.log(`Agent View: ${err.message}`);  // never fail the package transaction
      }
    } else {
      console.error("usage: patch.mjs status|install|restore|after-update [--resources DIR] [--server PATH] [--pacman-hook]");
      return 64;
    }
    return 0;
  } catch (err) {
    if (err.code === "EACCES" || err.code === "EPERM") {
      console.error(`${err.message}\nThis writes under ${opts.resources}; run it with sudo.`);
      return 1;
    }
    console.error(err.message);
    return err instanceof Refusal ? err.code : 1;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(fs.realpathSync(process.argv[1])).href) {
  process.exitCode = main(process.argv.slice(2));
}
