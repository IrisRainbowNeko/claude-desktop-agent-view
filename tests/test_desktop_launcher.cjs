"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const { createLauncher, ensureServer, proxyRequest, menuItem } = require("../desktop-patch/launcher.cjs");

function fixture(startServer = async () => {}) {
  const calls = [];
  const errors = [];
  const app = new EventEmitter();
  app.commandLine = { hasSwitch: () => false };
  const launcher = createLauncher({
    electron: { app,
      BrowserWindow: class { constructor() { throw new Error("must not open a window"); } },
      dialog: { showErrorBox: (...args) => errors.push(args) } },
    toggleSidebar: (event) => calls.push(event),
    startServer,
  });
  return { launcher, calls, errors, app };
}

test("menu toggles the native sidebar and forwards its focused-pane event", async () => {
  const f = fixture();
  const event = { source: "menu" };
  await f.launcher.menuItem.click(null, null, event);
  assert.deepEqual(f.calls, [event]);
  assert.equal(f.launcher.menuItem.accelerator, "CmdOrCtrl+Alt+G");
});

test("toolbar server is started before any menu action", async () => {
  let starts = 0;
  const f = fixture(async () => { starts++; });
  await new Promise((r) => setImmediate(r));
  assert.equal(starts, 1);
  assert.deepEqual(f.calls, []);
});

test("terminal action supplies the native menu event defaults", async () => {
  const f = fixture();
  await f.launcher.open();
  assert.deepEqual(f.calls, [{ triggeredByAccelerator: false }]);
});

test("server failure never toggles a blank pane or creates a window", async () => {
  const f = fixture(async () => { throw new Error("port occupied"); });
  await f.launcher.menuItem.click();
  assert.deepEqual(f.errors, [["Agent View", "port occupied"]]);
  assert.deepEqual(f.calls, []);
});

test("concurrent menu actions share startup and toggle only once", async () => {
  let starts = 0;
  let release;
  const ready = new Promise((r) => { release = r; });
  const f = fixture(async () => { starts++; await ready; });
  const a = f.launcher.menuItem.click();
  const b = f.launcher.menuItem.click();
  await new Promise((r) => setImmediate(r));
  assert.equal(starts, 1);
  release();
  await Promise.all([a, b]);
  assert.equal(f.calls.length, 1);
});

test("an existing Agent View is reused; another service is never replaced", async () => {
  const neverSpawn = () => { throw new Error("unexpected spawn"); };
  await ensureServer({ probe: async () => ({ events: [] }), spawnServer: neverSpawn });
  await assert.rejects(ensureServer({ probe: async () => ({ other: true }), spawnServer: neverSpawn }),
    /not Agent View/);
});

test("a missing server starts locally, without a remote command", async () => {
  let probes = 0;
  let call;
  let detached = false;
  await ensureServer({ server: __filename,
    probe: async () => {
      if (probes++ === 0) throw Object.assign(new Error("offline"), { code: "ECONNREFUSED" });
      return { events: [] };
    },
    wait: async () => {},
    spawnServer: (...args) => {
      call = args;
      const child = new EventEmitter();
      child.unref = () => { detached = true; };
      process.nextTick(() => child.emit("spawn"));
      return child;
    },
  });
  assert.equal(call[0], "python3");
  assert.deepEqual(call[1], [__filename, "--host", "127.0.0.1", "--port", "7777"]);
  assert.equal(detached, true);
});

test("multiple menu rebuilds register only one terminal listener", () => {
  const app = new EventEmitter();
  app.commandLine = { hasSwitch: () => false };
  const options = { electron: { app }, toggleSidebar: () => {}, startServer: async () => {} };
  assert.equal(menuItem(options), menuItem(options));
  assert.equal(app.listenerCount("second-instance"), 1);
});

test("repeated native second-instance events toggle the sidebar only once", async () => {
  const f = fixture();
  f.app.emit("second-instance", null, ["claude-desktop", "--agent-view"]);
  await new Promise((r) => setTimeout(r, 100));
  f.app.emit("second-instance", null, ["claude-desktop", "--agent-view"]);
  await new Promise((r) => setTimeout(r, 400));
  assert.equal(f.calls.length, 1);
});

test("isolated app proxy streams only read-only Agent View endpoints", async () => {
  let call;
  const electron = { net: { fetch: async (...args) => {
    call = args;
    return new Response("data: hello\n\n", { headers: { "Content-Type": "text/event-stream" } });
  } } };
  const result = await proxyRequest(new Request("app://agent-view/events"), electron);
  assert.equal(await result.text(), "data: hello\n\n");
  assert.equal(result.headers.get("content-type"), "text/event-stream");
  assert.deepEqual(call, ["http://127.0.0.1:7777/events", { credentials: "omit", redirect: "manual" }]);
  for (const url of ["app://localhost/", "http://127.0.0.1:7777/", "app://agent-view:80/",
    "app://agent-view/etc/passwd", "app://agent-view/event", "app://user@agent-view/"]) {
    assert.equal((await proxyRequest({ url, method: "GET" }, electron)).status, 403);
  }
  assert.equal((await proxyRequest(new Request("app://agent-view/", { method: "POST" }), electron)).status, 403);
  assert.equal((await proxyRequest(new Request("app://agent-view/", { headers: { Origin: "https://example.com" } }), electron)).status, 403);
});

test("app proxy redirects stay isolated and cannot escape to another service", async () => {
  const electron = { net: { fetch: async () => new Response(null, { status: 302,
    headers: { Location: "/?embedded=1#s=90e4cd6e-bd4e-4ee1-a0c6-6d7d7c8dfd39" } }) } };
  const result = await proxyRequest(new Request("app://agent-view/?desktop_session=local_test"), electron);
  assert.equal(result.headers.get("location"), "app://agent-view/?embedded=1#s=90e4cd6e-bd4e-4ee1-a0c6-6d7d7c8dfd39");
  for (const location of ["https://example.com/", "http://127.0.0.1:8888/", "/event"]) {
    electron.net.fetch = async () => new Response(null, { status: 302, headers: { Location: location } });
    assert.equal((await proxyRequest(new Request("app://agent-view/"), electron)).status, 502);
  }
  electron.net.fetch = async () => { throw new Error("offline"); };
  assert.equal((await proxyRequest(new Request("app://agent-view/"), electron)).status, 503);
});
