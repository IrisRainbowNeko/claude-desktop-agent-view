"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const Nav = require("../static/navigation.js");
const node = (id, parent = "main", status = "done", extra = {}) => ({ id, parent, status, label: id, ...extra });

test("one parent with 2000 running children has a fixed 20-node page budget", () => {
  const index = Nav.buildIndex([node("main", null), ...Array.from({ length: 2000 }, (_, i) => node(`task-${i}`, "main", "running"))]);
  const seen = new Set();
  for (let page = 0; page < 100; page++) {
    const result = Nav.page(index, { focus: "main", filter: "all", page });
    assert.equal(result.ids.length, 20);
    result.ids.forEach(id => seen.add(id));
  }
  assert.equal(seen.size, 2000);
  assert.equal(Nav.page(index, { focus: "main", page: 500 }).page, 99);
  assert.equal(index.stats.get("main").running, 2000);
});

test("deep search covers the whole session and locates the actual parent and page", () => {
  const nodes = [node("main", null), node("branch"), ...Array.from({ length: 75 }, (_, i) =>
    node(`task-${String(i).padStart(2, "0")}`, "branch", "done", { tool: i === 65 ? { name: "Read", brief: "罕见配置规则" } : null }))];
  const index = Nav.buildIndex(nodes);
  const found = Nav.page(index, { focus: "main", filter: "all", query: "罕见配置规则" });
  assert.deepEqual(found.ids, ["task-65"]);
  const location = Nav.locate(index, "task-65");
  assert.equal(location.focus, "branch");
  assert.equal(location.page, 3);
  assert.deepEqual(location.path, ["main", "branch", "task-65"]);
  assert.ok(Nav.page(index, location).ids.includes("task-65"));
});

test("filters keep completed ancestors with active descendants and historical errors do not mean failure", () => {
  const index = Nav.buildIndex([node("main", null), node("parent"), node("child", "parent", "waiting"),
    node("historical", "main", "done", { errors: 5 }), node("old", "main", "stale")]);
  assert.deepEqual(Nav.page(index, { focus: "main", filter: "attention" }).ids, ["old", "parent"]);
  assert.deepEqual(Nav.page(index, { focus: "main", filter: "waiting" }).ids, ["parent"]);
  assert.deepEqual(Nav.page(index, { focus: "parent", filter: "waiting" }).ids, ["child"]);
});

test("live updates preserve sibling order and append newly discovered children", () => {
  const before = Nav.buildIndex([node("main", null), node("b"), node("c")]);
  const after = Nav.buildIndex([node("a", "main", "waiting", { started: 1 }), node("c", "main", "running"), node("b"), node("main", null)], before);
  assert.deepEqual(after.children.get("main"), ["b", "c", "a"]);
});

test("cycles, missing parents, duplicate ids and deep chains cannot break navigation", () => {
  const input = [node("main", null), node("a", "b"), node("b", "a"), node("orphan", "missing"), node("orphan", "a")];
  let parent = "main";
  for (let i = 0; i < 1500; i++) { input.push(node(`deep-${i}`, parent)); parent = `deep-${i}`; }
  const index = Nav.buildIndex(input);
  assert.equal(new Set(index.order).size, index.nodes.size);
  assert.equal(Nav.pathTo(index, parent).length, 1501);
  assert.equal(Nav.pathTo(index, "a")[0], "main");
  assert.equal(Nav.pathTo(index, "orphan")[0], "main");
});

test("Windows and POSIX project labels work in the common frontend", () => {
  assert.equal(Nav.basename("D:\\code\\ra2\\platform\\"), "platform");
  assert.equal(Nav.basename("/home/user/repo/"), "repo");
});
