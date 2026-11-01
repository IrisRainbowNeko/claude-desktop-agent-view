// Shared by the single-document dashboard on Windows, macOS and Linux.
(function (root) {
  "use strict";
  const PAGE_SIZE = 20;
  const finished = (s) => ["done", "idle", "ended"].includes(s);
  const attention = (s) => ["running", "waiting", "stale"].includes(s);
  const matchesStatus = (n, filter) => filter === "all" || (filter === "attention" ? attention(n.status)
    : filter === "finished" ? finished(n.status) : n.status === filter);
  const basename = (p) => String(p || "").replace(/[\\/]+$/, "").split(/[\\/]/).pop() || p || "";

  function buildIndex(input, previous) {
    const nodes = new Map();
    for (const n of input) if (n && typeof n.id === "string" && !nodes.has(n.id)) nodes.set(n.id, n);
    if (!nodes.has("main")) nodes.set("main", { id: "main", parent: null, label: "主会话", status: "idle" });
    const parents = new Map(), children = new Map();
    for (const n of nodes.values()) {
      if (n.id === "main") continue;
      parents.set(n.id, n.parent && n.parent !== n.id && nodes.has(n.parent) ? n.parent : "main");
    }
    // Normalize malformed cycles and orphans without recursive traversal.
    const done = new Set(["main"]);
    for (const id of nodes.keys()) {
      let cursor = id;
      const path = new Set();
      while (!done.has(cursor)) {
        if (path.has(cursor)) { parents.set(cursor, "main"); break; }
        path.add(cursor);
        cursor = parents.get(cursor) || "main";
      }
      for (const item of path) done.add(item);
    }
    for (const [id, parent] of parents) {
      if (!children.has(parent)) children.set(parent, []);
      children.get(parent).push(id);
    }
    for (const [parent, ids] of children) {
      const old = new Map((previous?.children.get(parent) || []).map((id, i) => [id, i]));
      ids.sort((a, b) => {
        if (old.has(a) || old.has(b)) return (old.get(a) ?? Infinity) - (old.get(b) ?? Infinity);
        return (nodes.get(a).started || 0) - (nodes.get(b).started || 0) || a.localeCompare(b);
      });
    }
    const order = ["main"];
    for (let i = 0; i < order.length; i++) {
      for (const child of children.get(order[i]) || []) order.push(child);
    }
    const stats = new Map();
    for (let i = order.length - 1; i >= 0; i--) {
      const id = order[i], n = nodes.get(id);
      const count = { total: 1, running: 0, waiting: 0, stale: 0, finished: 0, attention: 0 };
      if (n.status in count) count[n.status]++;
      if (finished(n.status)) count.finished++;
      if (attention(n.status)) count.attention++;
      for (const child of children.get(id) || []) {
        for (const key of Object.keys(count)) count[key] += stats.get(child)[key];
      }
      stats.set(id, count);
    }
    return { nodes, parents, children, stats, order };
  }

  function pathTo(index, id) {
    const result = [];
    let current = index.nodes.has(id) ? id : "main";
    while (current) { result.push(current); current = index.parents.get(current); }
    return result.reverse();
  }

  function candidates(index, focus, filter, query) {
    query = query.trim().toLocaleLowerCase();
    if (query) return index.order.filter(id => {
      const n = index.nodes.get(id);
      return id !== "main" && matchesStatus(n, filter) && [n.id, n.label, n.agent_type, n.tool?.name, n.tool?.brief]
        .some(v => String(v || "").toLocaleLowerCase().includes(query));
    });
    return (index.children.get(focus) || []).filter(id => {
      const stats = index.stats.get(id);
      return filter === "all" || (filter === "attention" ? stats.attention > 0
        : filter === "finished" ? stats.finished > 0 : (stats[filter] || 0) > 0);
    });
  }

  function page(index, options) {
    const focus = index.nodes.has(options.focus) ? options.focus : "main";
    const ids = candidates(index, focus, options.filter || "all", options.query || "");
    const pages = Math.max(1, Math.ceil(ids.length / PAGE_SIZE));
    const number = Math.max(0, Math.min(Number.isInteger(options.page) ? options.page : 0, pages - 1));
    const offset = number * PAGE_SIZE;
    return { focus, ids: ids.slice(offset, offset + PAGE_SIZE), total: ids.length,
      page: number, pages, offset, size: PAGE_SIZE };
  }

  function locate(index, id, filter = "all") {
    if (!index.nodes.has(id)) return null;
    const focus = index.parents.get(id) || "main";
    const ids = candidates(index, focus, filter, "");
    return { focus, filter, page: Math.max(0, Math.floor(ids.indexOf(id) / PAGE_SIZE)), path: pathTo(index, id) };
  }

  const api = { PAGE_SIZE, finished, attention, matchesStatus, basename, buildIndex, pathTo, candidates, page, locate };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.AgentNavigation = api;
})(typeof window === "undefined" ? globalThis : window);
