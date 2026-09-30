// Minimal ASAR reader/writer, enough to replace or add a few files without dependencies.
//
// Layout: [u32 4][u32 N] then an N-byte Chromium pickle holding the JSON header
// ([u32 payload][u32 len][len bytes][pad to 4]), then file data. Each file entry has
// {size, offset (string, relative to the data start), integrity?, executable?} or
// {unpacked: true} for files kept in app.asar.unpacked, which are left alone here.
import crypto from "node:crypto";
import fs from "node:fs";

const BLOCK = 4 << 20;
const sha256 = (buf) => crypto.createHash("sha256").update(buf).digest("hex");

export function readAsar(buf) {
  if (buf.length < 16 || buf.readUInt32LE(0) !== 4) throw new Error("not an ASAR archive");
  const size = buf.readUInt32LE(4);
  const len = buf.readUInt32LE(12);
  const header = JSON.parse(buf.subarray(16, 16 + len).toString("utf8"));
  return { header, data: buf.subarray(8 + size) };
}

function entry(header, name, create = false) {
  let node = header;
  const parts = name.split("/");
  for (const [i, part] of parts.entries()) {
    if (!node.files) throw new Error(`${name}: not a directory`);
    if (!node.files[part]) {
      if (!create) return null;
      node.files[part] = i < parts.length - 1 ? { files: {} } : {};
    }
    node = node.files[part];
  }
  return node;
}

export function readFile({ header, data }, name) {
  const e = entry(header, name);
  if (!e || e.files || e.unpacked || e.link) return null;
  const off = Number(e.offset);
  return data.subarray(off, off + e.size);
}

function integrity(buf) {
  const blocks = [];
  for (let i = 0; i < buf.length || i === 0; i += BLOCK) blocks.push(sha256(buf.subarray(i, i + BLOCK)));
  return { algorithm: "SHA256", hash: sha256(buf), blockSize: BLOCK, blocks };
}

// Returns a new archive with `changes` ({path: Buffer}) written. Unchanged files keep their
// bytes and offsets; new contents are appended after the existing data.
export function writeAsar({ header, data }, changes) {
  header = structuredClone(header);
  const tail = [];
  let end = data.length;
  for (const [name, buf] of Object.entries(changes)) {
    const e = entry(header, name, true);
    if (e.files || e.unpacked || e.link) throw new Error(`${name}: cannot replace this entry`);
    Object.assign(e, { size: buf.length, offset: String(end), integrity: integrity(buf) });
    tail.push(buf);
    end += buf.length;
  }
  const json = Buffer.from(JSON.stringify(header), "utf8");
  const pickle = Buffer.alloc(8 + json.length + ((4 - (json.length % 4)) % 4));
  pickle.writeUInt32LE(pickle.length - 4, 0);
  pickle.writeUInt32LE(json.length, 4);
  json.copy(pickle, 8);
  const sizes = Buffer.alloc(8);
  sizes.writeUInt32LE(4, 0);
  sizes.writeUInt32LE(pickle.length, 4);
  return Buffer.concat([sizes, pickle, data, ...tail]);
}

export const loadAsar = (path) => readAsar(fs.readFileSync(path));
export { sha256 };
