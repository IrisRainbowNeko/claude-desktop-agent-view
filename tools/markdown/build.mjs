// Rebuilds static/vendor/markdown.js and static/vendor/katex.css (committed, so users need no npm).
//   cd tools/markdown && npm install && node build.mjs
import { build } from "esbuild";
import { readFileSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const out = fileURLToPath(new URL("../../static/vendor/", import.meta.url));

await build({
  entryPoints: ["entry.js"], bundle: true, minify: true, format: "iife", globalName: "AgentMarkdown",
  target: "es2020", outfile: join(out, "markdown.js"), legalComments: "eof",
});

// KaTeX CSS with its woff2 fonts inlined as data: URIs (the page is served as a single document).
const katexDir = dirname(require.resolve("katex/package.json"));
const css = readFileSync(join(katexDir, "dist/katex.min.css"), "utf8").replace(
  /src:url\(fonts\/([^)]+?\.woff2)\) format\("woff2"\)(,url\([^)]+\) format\("[^"]+"\))*/g,
  (_, font) => `src:url(data:font/woff2;base64,${readFileSync(join(katexDir, "dist/fonts", font)).toString("base64")}) format("woff2")`);
writeFileSync(join(out, "katex.css"), `/* KaTeX ${require("katex/package.json").version}, MIT License */\n` + css);
console.log("wrote", out);
