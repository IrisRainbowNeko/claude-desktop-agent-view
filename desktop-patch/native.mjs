#!/usr/bin/env node
// Native Windows/macOS sidebar patch. Operates on a user-owned application copy.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { readAsar, readFile, writeAsar, sha256, headerHash } from "./asar.mjs";
import { launcherSource, replaceEach } from "./patch.mjs";
import { NATIVE_BUILDS } from "./native-builds.mjs";

const REPO = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const LAUNCHER = ".vite/build/agentViewLauncher.js";
const STATE = "agent-view-native-patch";

export function prepareNative(appBytes, ionBytes, { platform, server, builds = NATIVE_BUILDS }) {
  const app = readAsar(appBytes), ion = readAsar(ionBytes);
  const version = JSON.parse(readFile(app, "package.json")).version;
  const build = builds[platform]?.[version];
  if (!build) throw new Error(`${platform} Claude Desktop ${version} has not been reviewed; nothing changed.`);
  if (readFile(app, LAUNCHER)) throw new Error("An Agent View launcher already exists; restore it first.");
  const chunk = readFile(app, build.chunk);
  if (!chunk || (build.mainHash && sha256(chunk) !== build.mainHash)) {
    throw new Error("Main-process chunk does not match the reviewed native build; nothing changed.");
  }
  const frontend = {};
  for (const [file, hash] of Object.entries(build.dependencies || {})) {
    const original = readFile(ion, "assets/v1/" + file);
    if (!original || sha256(original) !== hash) throw new Error(`Unreviewed renderer dependency ${file}; nothing changed.`);
  }
  for (const [file, [hash, changes]] of Object.entries(build.frontend)) {
    const original = readFile(ion, "assets/v1/" + file);
    if (!original || sha256(original) !== hash) throw new Error(`Unreviewed renderer ${file}; nothing changed.`);
    frontend["assets/v1/" + file] = Buffer.from(replaceEach(original.toString("utf8"), changes, file));
  }
  return { version,
    app: writeAsar(app, {
      [build.chunk]: Buffer.from(replaceEach(chunk.toString("utf8"), build.main, build.chunk)),
      [LAUNCHER]: Buffer.from(launcherSource(path.resolve(server))),
    }),
    ion: writeAsar(ion, frontend),
  };
}

export function updateWindowsIntegrity(executable, changes) {
  if (executable.toString("ascii", 0, 2) !== "MZ") throw new Error("Not a Windows executable.");
  const result = Buffer.from(executable);
  for (const [name, before, after] of changes) {
    // Match the full embedded resource entry, never an arbitrary occurrence of a hash.
    const record = Buffer.from(JSON.stringify({ file: `resources\\${name}`, alg: "SHA256", value: headerHash(before) }));
    const at = result.indexOf(record);
    if (at < 0 || result.indexOf(record, at + 1) >= 0) throw new Error(`Missing or ambiguous embedded integrity for ${name}; nothing changed.`);
    const replacement = Buffer.from(JSON.stringify({ file: `resources\\${name}`, alg: "SHA256", value: headerHash(after) }));
    replacement.copy(result, at);
  }
  // The edited PE is an unsigned development copy. Do not leave an invalid
  // Authenticode signature or install a replacement root certificate.
  const pe = result.readUInt32LE(0x3c);
  if (result.toString("ascii", pe, pe + 4) !== "PE\0\0") throw new Error("Invalid PE header.");
  const optional = pe + 24, magic = result.readUInt16LE(optional);
  if (![0x10b, 0x20b].includes(magic)) throw new Error("Unknown PE optional header.");
  const directories = optional + (magic === 0x20b ? 112 : 96);
  result.fill(0, directories + 8 * 4, directories + 8 * 5);
  return result;
}

function atomic(file, bytes) {
  const temp = `${file}.agent-view-${process.pid}.tmp`;
  try { fs.writeFileSync(temp, bytes); fs.renameSync(temp, file); }
  finally { if (fs.existsSync(temp)) fs.unlinkSync(temp); }
}

function locations(resources, platform) {
  const files = {
    "app.asar": path.join(resources, "app.asar"),
    "ion-dist.asar": path.join(resources, "ion-dist.asar"),
  };
  if (platform === "win32") files["claude.exe"] = path.join(resources, "..", "claude.exe");
  return { files, state: path.join(resources, STATE) };
}

function assertWritableCopy(resources, platform) {
  const real = fs.realpathSync(resources);
  if (platform === "win32" && /[\\/]WindowsApps[\\/]/i.test(real)) {
    throw new Error("MSIX installations are immutable. Use install-windows.ps1 to patch a separate copy.");
  }
  if (platform === "darwin" && real.startsWith("/Applications/")) {
    throw new Error("Use install-macos.py to prepare a copy under ~/Applications first.");
  }
}

export function nativeStatus({ resources, platform = process.platform, builds = NATIVE_BUILDS }) {
  const { files, state } = locations(resources, platform);
  const app = fs.readFileSync(files["app.asar"]);
  const version = JSON.parse(readFile(readAsar(app), "package.json")).version;
  if (fs.existsSync(path.join(state, "state.json"))) {
    const saved = JSON.parse(fs.readFileSync(path.join(state, "state.json"), "utf8"));
    const intact = Object.entries(saved.installed).every(([name, hash]) =>
      files[name] && fs.existsSync(files[name]) && sha256(fs.readFileSync(files[name])) === hash);
    return `Claude Desktop ${version} (${platform}): Agent View ${intact ? "installed" : "files changed; restore refused"}.`;
  }
  return `Claude Desktop ${version} (${platform}): ${builds[platform]?.[version] ? "reviewed version; not installed" : "unreviewed version"}.`;
}

export function installNative({ resources, platform = process.platform, server = path.join(REPO, "server.py"), builds = NATIVE_BUILDS }) {
  assertWritableCopy(resources, platform);
  const { files, state } = locations(resources, platform);
  if (fs.existsSync(state)) throw new Error("A backup already exists. Restore before reinstalling.");
  if (!fs.statSync(server).isFile()) throw new Error(`No server.py at ${server}`);
  const original = Object.fromEntries(Object.entries(files).map(([name, file]) => [name, fs.readFileSync(file)]));
  const prepared = prepareNative(original["app.asar"], original["ion-dist.asar"], { platform, server, builds });
  const updated = { "app.asar": prepared.app, "ion-dist.asar": prepared.ion };
  if (platform === "win32") updated["claude.exe"] = updateWindowsIntegrity(original["claude.exe"], [
    ["app.asar", original["app.asar"], prepared.app],
    ["ion-dist.asar", original["ion-dist.asar"], prepared.ion],
  ]);
  fs.mkdirSync(state);
  const written = [];
  try {
    for (const [name, bytes] of Object.entries(original)) fs.writeFileSync(path.join(state, name), bytes);
    atomic(path.join(state, "state.json"), JSON.stringify({ version: prepared.version, platform,
      original: Object.fromEntries(Object.entries(original).map(([n, b]) => [n, sha256(b)])),
      installed: Object.fromEntries(Object.entries(updated).map(([n, b]) => [n, sha256(b)])),
    }, null, 2) + "\n");
    for (const [name, bytes] of Object.entries(updated)) { atomic(files[name], bytes); written.push(name); }
  } catch (err) {
    for (const name of written.reverse()) atomic(files[name], original[name]);
    fs.rmSync(state, { recursive: true, force: true });
    throw err;
  }
  return `Patched Claude Desktop ${prepared.version} (${platform}). ` + (platform === "darwin"
    ? "Re-sign the application copy before launching." : "The application copy is unsigned.");
}

export function restoreNative({ resources, platform = process.platform }) {
  assertWritableCopy(resources, platform);
  const { files, state } = locations(resources, platform);
  if (!fs.existsSync(state)) return "Nothing to restore.";
  const saved = JSON.parse(fs.readFileSync(path.join(state, "state.json"), "utf8"));
  // Validate every file and backup before restoring any of them. Keep the backup
  // if an update or another tool changed even one file.
  const originals = {};
  if (saved.platform !== platform || Object.keys(saved.installed).length !== Object.keys(files).length) {
    throw new Error("Backup platform or file set does not match; nothing changed.");
  }
  for (const [name, file] of Object.entries(files)) {
    if (!fs.existsSync(file) || sha256(fs.readFileSync(file)) !== saved.installed[name]) {
      throw new Error(`${name} changed since install; backup retained and nothing restored.`);
    }
    const bytes = fs.readFileSync(path.join(state, name));
    if (sha256(bytes) !== saved.original[name]) throw new Error(`Damaged backup ${name}; nothing restored.`);
    originals[name] = bytes;
  }
  const current = Object.fromEntries(Object.entries(files).map(([n, f]) => [n, fs.readFileSync(f)]));
  const written = [];
  try {
    for (const [name, bytes] of Object.entries(originals)) { atomic(files[name], bytes); written.push(name); }
  } catch (err) {
    for (const name of written.reverse()) atomic(files[name], current[name]);
    throw err;
  }
  fs.rmSync(state, { recursive: true });
  return "Restored the original files." + (platform === "darwin" ? " Re-sign the application copy before launching." : "");
}

function main(argv) {
  const [command = "status", ...args] = argv;
  const options = {};
  for (let i = 0; i < args.length; i += 2) {
    if (!["--resources", "--server", "--platform"].includes(args[i]) || !args[i + 1]) throw new Error("Expected --resources DIR [--server PATH] [--platform win32|darwin].");
    options[args[i].slice(2)] = args[i + 1];
  }
  if (!options.resources) throw new Error("Pass --resources pointing to the application copy's resources directory.");
  if (!["win32", "darwin"].includes(options.platform || process.platform)) throw new Error("Use patch.mjs for Linux.");
  const action = { status: nativeStatus, install: installNative, restore: restoreNative }[command];
  if (!action) throw new Error("Commands: status, install, restore.");
  console.log(action(options));
}

if (process.argv[1] && import.meta.url === pathToFileURL(fs.realpathSync(process.argv[1])).href) {
  try { main(process.argv.slice(2)); }
  catch (err) { console.error(err.message); process.exitCode = 1; }
}
