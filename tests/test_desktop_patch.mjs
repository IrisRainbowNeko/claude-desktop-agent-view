import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { readAsar, writeAsar, readFile, sha256 } from "../desktop-patch/asar.mjs";
import { install, restore, afterUpdate, status, launcherSource } from "../desktop-patch/patch.mjs";

const LAUNCHER = 'const path = require("node:path");\nconst SERVER = process.env.AGENT_VIEW_SERVER || path.join(__dirname, "..", "server.py");\n';
const FRONT = "export const pane=a=>a.local;";
const BUILDS = { "9.9.9": {
  chunk: ".vite/build/index.js",
  main: [["menu:[", "menu:[agentViewLauncher,"]],
  frontend: { "pane.js": [sha256(Buffer.from(FRONT)), [["a.local", "a.local||a.ssh"]]] },
} };

function archive(files) {
  let data = Buffer.alloc(0);
  const header = { files: {} };
  for (const [name, text] of Object.entries(files)) {
    const buf = Buffer.from(text);
    let node = header;
    const parts = name.split("/");
    for (const part of parts.slice(0, -1)) node = (node.files[part] ??= { files: {} });
    node.files[parts.at(-1)] = { size: buf.length, offset: String(data.length) };
    data = Buffer.concat([data, buf]);
  }
  return writeAsar({ header, data }, {});
}

function desktop(version = "9.9.9", front = FRONT) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "agent-view-patch-"));
  fs.mkdirSync(path.join(dir, "ion-dist/assets/v1"), { recursive: true });
  const asar = archive({ "package.json": JSON.stringify({ version }), ".vite/build/index.js": "x={menu:[view]}" });
  fs.writeFileSync(path.join(dir, "app.asar"), asar);
  fs.writeFileSync(path.join(dir, "ion-dist/assets/v1/pane.js"), front);
  return { dir, asar, opts: { resources: dir, builds: BUILDS, server: "/opt/agent view/server.py", launcher: LAUNCHER } };
}

const pane = (d) => fs.readFileSync(path.join(d.dir, "ion-dist/assets/v1/pane.js"), "utf8");

test("install patches the archive and frontend, restore brings back the exact bytes", () => {
  const d = desktop();
  assert.match(status(d.opts).text, /not installed \(supported\)/);
  install(d.opts);
  const a = readAsar(fs.readFileSync(path.join(d.dir, "app.asar")));
  assert.equal(readFile(a, ".vite/build/index.js").toString(), "x={menu:[agentViewLauncher,view]}");
  assert.match(readFile(a, ".vite/build/agentViewLauncher.js").toString(), /\|\| "\/opt\/agent view\/server.py";/);
  assert.equal(pane(d), "export const pane=a=>a.local||a.ssh;");
  assert.match(status(d.opts).text, /installed\./);
  install(d.opts);  // reinstall rebuilds from the saved original, never patching twice
  assert.equal(readFile(readAsar(fs.readFileSync(path.join(d.dir, "app.asar"))), ".vite/build/index.js").toString(),
    "x={menu:[agentViewLauncher,view]}");
  restore(d.opts);
  assert.ok(fs.readFileSync(path.join(d.dir, "app.asar")).equals(d.asar));
  assert.equal(pane(d), FRONT);
  assert.ok(!fs.existsSync(path.join(d.dir, "agent-view-patch")));
});

test("unreviewed versions, unknown frontends and foreign patches are left untouched", () => {
  for (const d of [desktop("10.0.0"), desktop("9.9.9", "changed")]) {
    assert.throws(() => install(d.opts), /not been reviewed|Unknown frontend/);
    assert.ok(fs.readFileSync(path.join(d.dir, "app.asar")).equals(d.asar));
    assert.ok(!fs.existsSync(path.join(d.dir, "agent-view-patch")));
  }
  const d = desktop();
  fs.writeFileSync(path.join(d.dir, "app.asar"), archive({ "package.json": '{"version":"9.9.9"}',
    ".vite/build/index.js": "x={menu:[agentViewLauncher,view]}" }));
  assert.throws(() => install(d.opts), /another|did not install/);
  assert.match(status(d.opts).text, /another installer/);
});

test("an ambiguous anchor refuses the whole install", () => {
  const d = desktop();
  fs.writeFileSync(path.join(d.dir, "app.asar"), archive({ "package.json": '{"version":"9.9.9"}',
    ".vite/build/index.js": "menu:[menu:[" }));
  assert.throws(() => install(d.opts), /exactly once/);
  assert.equal(pane(d), FRONT);
});

test("after an update to an unreviewed build, the new files are kept and the stale backup dropped", () => {
  const d = desktop();
  install(d.opts);
  const updated = archive({ "package.json": '{"version":"10.0.0"}', ".vite/build/index.js": "new" });
  fs.writeFileSync(path.join(d.dir, "app.asar"), updated);
  fs.writeFileSync(path.join(d.dir, "ion-dist/assets/v1/pane.js"), "new frontend");
  assert.match(afterUpdate(d.opts), /not been reviewed/);
  assert.ok(fs.readFileSync(path.join(d.dir, "app.asar")).equals(updated));
  assert.ok(!fs.existsSync(path.join(d.dir, "agent-view-patch")));
});

test("after an update to a reviewed build, the patch is applied again", () => {
  const d = desktop();
  install(d.opts);
  fs.writeFileSync(path.join(d.dir, "app.asar"), d.asar);
  fs.writeFileSync(path.join(d.dir, "ion-dist/assets/v1/pane.js"), FRONT);
  assert.match(afterUpdate(d.opts), /Installed/);
  assert.equal(pane(d), "export const pane=a=>a.local||a.ssh;");
  assert.match(afterUpdate(d.opts), /still installed/);
});

test("the repo launcher has the line the installer rewrites", () => {
  assert.match(launcherSource("/srv/x/server.py"), /AGENT_VIEW_SERVER \|\| "\/srv\/x\/server.py";/);
});
