"use strict";

const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");
const { spawn } = require("node:child_process");

const ORIGIN = "http://127.0.0.1:7777";
// patch.mjs replaces this line with the absolute path of this checkout when it copies the
// file into Desktop's app.asar, where __dirname points inside the archive.
const SERVER = process.env.AGENT_VIEW_SERVER || path.join(__dirname, "..", "server.py");
const PANE_ORIGIN = "app://agent-view";
const READ_PATHS = new Set(["/", "/index.html", "/events", "/api/sessions", "/api/session", "/api/transcript", "/api/hooklog"]);

async function proxyRequest(request, electron) {
  const url = new URL(request.url);
  const origin = request.headers?.get("Origin");
  if (url.protocol !== "app:" || url.hostname !== "agent-view" || url.port ||
      url.username || url.password || request.method !== "GET" || !READ_PATHS.has(url.pathname) ||
      (origin && origin !== PANE_ORIGIN && origin !== "app://localhost")) {
    return new Response(null, { status: 403 });
  }
  try {
    const target = new URL(url.pathname + url.search, ORIGIN);
    const upstream = await electron.net.fetch(target.href, { credentials: "omit", redirect: "manual" });
    const headers = new Headers(upstream.headers);
    const location = headers.get("location");
    if (location) {
      const redirect = new URL(location, target);
      if (redirect.origin !== ORIGIN || !READ_PATHS.has(redirect.pathname)) {
        return new Response(null, { status: 502 });
      }
      headers.set("location", PANE_ORIGIN + redirect.pathname + redirect.search + redirect.hash);
    }
    headers.delete("set-cookie");
    return new Response(upstream.body, { status: upstream.status, headers });
  } catch {
    return new Response("Agent View is unavailable. Close and reopen the pane after restarting Claude Desktop.", {
      status: 503, headers: { "Content-Type": "text/plain; charset=utf-8" },
    });
  }
}

function requestJson(url) {
  return new Promise((resolve, reject) => {
    const req = http.get(url, { timeout: 1500 }, (res) => {
      let body = "";
      res.setEncoding("utf8");
      res.on("data", (chunk) => {
        body += chunk;
        if (body.length > 1024 * 1024) req.destroy(new Error("Response too large"));
      });
      res.on("error", reject);
      res.on("end", () => {
        try {
          if (res.statusCode !== 200) throw new Error(`HTTP ${res.statusCode}`);
          resolve(JSON.parse(body));
        } catch (err) {
          reject(err);
        }
      });
    });
    req.on("timeout", () => req.destroy(new Error("Agent View request timed out")));
    req.on("error", reject);
  });
}

async function ensureServer({ probe = requestJson, spawnServer = spawn, server = SERVER,
                              wait = (ms) => new Promise((r) => setTimeout(r, ms)) } = {}) {
  const up = async () => {
    try {
      const result = await probe(`${ORIGIN}/api/hooklog`);
      if (!Array.isArray(result.events)) throw new Error("Port 7777 is not Agent View");
      return true;
    } catch (err) {
      if (err.code === "ECONNREFUSED") return false;
      throw err;
    }
  };
  if (await up()) return;
  await fs.promises.access(server, fs.constants.R_OK);
  const dir = path.join(os.homedir(), ".local", "state");
  await fs.promises.mkdir(dir, { recursive: true });
  const fd = fs.openSync(path.join(dir, "agent-view-launcher.log"), "a", 0o600);
  try {
    const child = spawnServer(process.env.AGENT_VIEW_PYTHON || "python3", [server, "--host", "127.0.0.1", "--port", "7777"], {
      cwd: path.dirname(server), detached: true, stdio: ["ignore", fd, fd],
    });
    await new Promise((resolve, reject) => {
      child.once("error", reject);
      child.once("spawn", () => { child.unref(); resolve(); });
    });
  } finally {
    fs.closeSync(fd);
  }
  for (let i = 0; i < 40; i++) {
    await wait(200);
    if (await up()) return;
  }
  throw new Error("Agent View did not start; see ~/.local/state/agent-view-launcher.log");
}

function createLauncher({ electron, toggleSidebar, logger,
                          startServer = ensureServer }) {
  let starting = null;
  let launching = null;
  let lastTerminalLaunch = -Infinity;

  function ready() {
    if (!starting) starting = Promise.resolve().then(startServer).finally(() => { starting = null; });
    return starting;
  }

  async function open(event = { triggeredByAccelerator: false }) {
    await ready();
    toggleSidebar(event);
    logger?.info("[AgentViewSidebar] Toggled the current session's sidebar");
  }

  function launch(event) {
    if (launching) return launching;
    launching = open(event).catch((err) => {
      logger?.warn("[AgentViewSidebar] Could not open dashboard", { error: err.message });
      electron.dialog.showErrorBox("Agent View", err.message);
    }).finally(() => { launching = null; });
    return launching;
  }

  // The renderer's toolbar can open the pane without invoking the native menu.
  ready().catch((err) => logger?.warn("[AgentViewSidebar] Startup failed", { error: err.message }));
  function launchTerminal() {
    const now = Date.now();
    if (now - lastTerminalLaunch < 2000) return;
    lastTerminalLaunch = now;
    setTimeout(launch, 350);
  }
  electron.app.on("second-instance", (_event, argv) => {
    if (argv.includes("--agent-view")) launchTerminal();
  });
  if (electron.app.commandLine.hasSwitch("agent-view")) {
    electron.app.whenReady().then(() => setTimeout(launchTerminal, 1500));
  }
  return { open, menuItem: { id: "agent-view-open", label: "Toggle Agent View Sidebar",
    accelerator: "CmdOrCtrl+Alt+G", click: (_item, _window, event) => launch(event) } };
}

function menuItem(options) {
  const key = Symbol.for("agent-view.sidebar.launcher");
  options.electron.app[key] ??= createLauncher(options);
  return options.electron.app[key].menuItem;
}

module.exports = { menuItem, createLauncher, ensureServer, requestJson, proxyRequest };
