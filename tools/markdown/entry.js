// Bundled into static/vendor/markdown.js: Markdown + MyST (directives, roles, colon fences,
// targets) + LaTeX math via KaTeX. Exposes window.AgentMarkdown.render(text) -> HTML string.
import MarkdownIt from "markdown-it";
import { dollarmathPlugin } from "markdown-it-dollarmath";
import { amsmathPlugin } from "markdown-it-amsmath";
import { docutilsPlugin } from "markdown-it-docutils";
import { colonFencePlugin, mystBlockPlugin } from "markdown-it-myst-extras";
import footnotePlugin from "markdown-it-footnote";
import deflistPlugin from "markdown-it-deflist";
import katex from "katex";

const tex = (src, displayMode) => {
  try {
    return katex.renderToString(src, { displayMode, throwOnError: false, output: "html", trust: false, strict: "ignore" });
  } catch {
    return `<code class="math-error">${src.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c])}</code>`;
  }
};

const md = new MarkdownIt({ html: false, linkify: true, breaks: false })
  .use(mystBlockPlugin)
  .use(colonFencePlugin)
  .use(docutilsPlugin)
  .use(dollarmathPlugin, { allow_space: true, allow_digits: false, double_inline: true, allow_labels: true,
                          renderer: (s, o) => tex(s, o.displayMode) })
  .use(amsmathPlugin, { renderer: (s) => tex(s, true) })
  .use(footnotePlugin)
  .use(deflistPlugin);

// Links open outside the pane.
const linkOpen = md.renderer.rules.link_open || ((t, i, o, e, s) => s.renderToken(t, i, o));
md.renderer.rules.link_open = (tokens, idx, opts, env, self) => {
  tokens[idx].attrSet("target", "_blank");
  tokens[idx].attrSet("rel", "noopener noreferrer");
  return linkOpen(tokens, idx, opts, env, self);
};

// {math} roles and ```{math} directives come out as raw TeX in .math elements; typeset them here.
function typeset(html) {
  if (!html.includes('class="math')) return html;
  const box = document.createElement("div");
  box.innerHTML = html;
  for (const el of box.querySelectorAll(".math:not(.katex-done)")) {
    if (el.querySelector(".katex")) continue;
    const display = el.tagName === "DIV" || el.classList.contains("block");
    el.innerHTML = tex(el.textContent, display);
    el.classList.add("katex-done");
  }
  return box.innerHTML;
}

export function render(text) {
  return typeset(md.render(text || ""));
}
