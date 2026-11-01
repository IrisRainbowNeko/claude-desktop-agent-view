import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import vm from "node:vm";
import { readAsar, writeAsar, readFile, sha256, headerHash } from "../desktop-patch/asar.mjs";
import { prepareNative, installNative, restoreNative, nativeStatus, updateWindowsIntegrity } from "../desktop-patch/native.mjs";
import { NATIVE_BUILDS } from "../desktop-patch/native-builds.mjs";

const FRONT = 'export const pane=e=>e.local;';
const builds = { win32: { "9.9.9": { chunk: ".vite/build/index.js",
  main: [["menu:[", "menu:[agentViewLauncher,"]],
  frontend: { "pane.js": [sha256(Buffer.from(FRONT)), [["e.local", "e.local||e.ssh"]]] },
} } };
const archive = (files) => writeAsar({ header: { files: {} }, data: Buffer.alloc(0) },
  Object.fromEntries(Object.entries(files).map(([f, t]) => [f, Buffer.from(t)])));

function executable(app, ion) {
  const pe = Buffer.alloc(512);
  pe.write("MZ"); pe.writeUInt32LE(0x80, 0x3c); pe.write("PE\0\0", 0x80);
  pe.writeUInt16LE(0x20b, 0x80 + 24);
  const cert = 0x80 + 24 + 112 + 8 * 4;
  pe.writeUInt32LE(0x1234, cert); pe.writeUInt32LE(123, cert + 4);
  return Buffer.concat([pe, Buffer.from(JSON.stringify([
    { file: "resources\\app.asar", alg: "SHA256", value: headerHash(app) },
    { file: "resources\\ion-dist.asar", alg: "SHA256", value: headerHash(ion) },
  ]))]);
}

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "agent-view-native-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const resources = path.join(root, "resources"); fs.mkdirSync(resources);
  const app = archive({ "package.json": '{"version":"9.9.9"}', ".vite/build/index.js": "x={menu:[view]}" });
  const ion = archive({ "assets/v1/pane.js": FRONT });
  const exe = executable(app, ion);
  fs.writeFileSync(path.join(resources, "app.asar"), app);
  fs.writeFileSync(path.join(resources, "ion-dist.asar"), ion);
  fs.writeFileSync(path.join(root, "claude.exe"), exe);
  const server = path.join(root, "server.py"); fs.writeFileSync(server, "");
  return { root, app, ion, exe, opts: { resources, platform: "win32", server, builds } };
}

test("native archives and both embedded Windows hashes install and restore exactly", (t) => {
  const f = fixture(t);
  installNative(f.opts);
  assert.match(nativeStatus(f.opts), /installed/);
  const app = fs.readFileSync(path.join(f.opts.resources, "app.asar"));
  const ion = fs.readFileSync(path.join(f.opts.resources, "ion-dist.asar"));
  const exe = fs.readFileSync(path.join(f.root, "claude.exe"));
  assert.ok(exe.includes(Buffer.from(headerHash(app))));
  assert.ok(exe.includes(Buffer.from(headerHash(ion))));
  assert.equal(exe.readUInt32LE(0x80 + 24 + 112 + 8 * 4), 0);
  assert.equal(readFile(readAsar(ion), "assets/v1/pane.js").toString(), "export const pane=e=>e.local||e.ssh;");
  assert.throws(() => installNative(f.opts), /backup already exists/);
  restoreNative(f.opts);
  assert.deepEqual(fs.readFileSync(path.join(f.opts.resources, "app.asar")), f.app);
  assert.deepEqual(fs.readFileSync(path.join(f.opts.resources, "ion-dist.asar")), f.ion);
  assert.deepEqual(fs.readFileSync(path.join(f.root, "claude.exe")), f.exe);
});

test("unreviewed versions, renderer dependencies and ambiguous hashes refuse before writes", (t) => {
  const f = fixture(t);
  assert.throws(() => prepareNative(f.app, f.ion, { ...f.opts, platform: "darwin" }), /not been reviewed/);
  const bad = structuredClone(builds); bad.win32["9.9.9"].dependencies = { "missing.js": "abc" };
  assert.throws(() => installNative({ ...f.opts, builds: bad }), /dependency/);
  assert.throws(() => updateWindowsIntegrity(Buffer.concat([f.exe, f.exe]), [["app.asar", f.app, f.app]]), /ambiguous/);
  assert.equal(fs.existsSync(path.join(f.opts.resources, "agent-view-native-patch")), false);
  assert.deepEqual(fs.readFileSync(path.join(f.root, "claude.exe")), f.exe);
});

test("an updated executable or damaged backup prevents every restore and retains recovery files", (t) => {
  const f = fixture(t); installNative(f.opts);
  const installed = fs.readFileSync(path.join(f.opts.resources, "app.asar"));
  fs.appendFileSync(path.join(f.root, "claude.exe"), "update");
  assert.throws(() => restoreNative(f.opts), /changed since install/);
  assert.deepEqual(fs.readFileSync(path.join(f.opts.resources, "app.asar")), installed);
  assert.equal(fs.existsSync(path.join(f.opts.resources, "agent-view-native-patch/app.asar")), true);
});

test("native SSH pane follows its session and keeps the original preview path for other panes", () => {
  const build = NATIVE_BUILDS.win32["4.4212.0"];
  const changes = build.frontend["c9bd7af9c-ChJIedqp.js"][1];
  const replacement = changes.find(([before]) => before.startsWith("let Et="))[1];
  const expression = replacement.slice("let Et=".length).replace(/:J5\(\$,154,$/, ":null");
  const render = (t, agentViewSsh, id) => vm.runInNewContext(expression, {
    t, agentViewSsh, j: { id }, z: (tag, props) => ({ tag, props }), encodeURIComponent,
  });
  for (const id of ["local_first", "local_second"]) {
    const frame = render("preview", true, id);
    assert.equal(frame.tag, "iframe");
    assert.equal(new URL(frame.props.src).searchParams.get("desktop_session"), id);
    assert.equal(frame.props.sandbox, "allow-scripts allow-same-origin");
    assert.equal(frame.props.referrerPolicy, "no-referrer");
  }
  assert.equal(render("preview", false, "ordinary_local"), null);
  assert.equal(render("terminal", true, "local_first"), null);
  const gate = changes.find(([before]) => before.startsWith("St="))[1];
  for (const agentViewSsh of [false, true]) {
    assert.equal(vm.runInNewContext(gate, { J1: () => false, xt: true, $: { 121: true }, agentViewSsh }), !agentViewSsh);
  }
});

for (const platform of ["win32", "darwin"]) {
  const resources = process.env[platform === "win32" ? "AGENT_VIEW_WINDOWS_RESOURCES" : "AGENT_VIEW_MACOS_RESOURCES"];
  test(`reviewed ${platform} application: apply all anchors and parse every patched bundle`, { skip: !resources }, (t) => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "agent-view-bundle-"));
    t.after(() => fs.rmSync(root, { recursive: true, force: true }));
    const p = prepareNative(fs.readFileSync(path.join(resources, "app.asar")),
      fs.readFileSync(path.join(resources, "ion-dist.asar")), { platform, server: path.resolve("server.py") });
    const build = NATIVE_BUILDS[platform][p.version];
    for (const [name, bytes] of [["main.cjs", readFile(readAsar(p.app), build.chunk)],
      ...Object.keys(build.frontend).map(f => [f + ".mjs", readFile(readAsar(p.ion), "assets/v1/" + f)])]) {
      const file = path.join(root, name); fs.writeFileSync(file, bytes);
      const result = spawnSync(process.execPath, ["--check", file], { encoding: "utf8" });
      assert.equal(result.status, 0, result.stderr);
    }
  });
}
