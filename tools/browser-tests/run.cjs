"use strict";
const assert = require("node:assert/strict");
const path = require("node:path");
const os = require("node:os");
const fs = require("node:fs");
const { spawn } = require("node:child_process");
const { createInterface } = require("node:readline");
if (process.env.AGENT_VIEW_TEST_TMP) {
  fs.mkdirSync(process.env.AGENT_VIEW_TEST_TMP, { recursive: true });
  os.tmpdir = () => process.env.AGENT_VIEW_TEST_TMP;
}
const { chromium } = require("playwright");
const repo = path.resolve(__dirname, "../..");

async function until(check, label) {
  for (let i = 0; i < 100; i++) {
    if (await check()) return;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw new Error("Timed out: " + label);
}

(async () => {
  const fixture = spawn(process.env.AGENT_VIEW_TEST_PYTHON || "python", ["-u", "tests/browser_fixture.py"], {
    cwd: repo, stdio: ["ignore", "pipe", "pipe"], windowsHide: true,
  });
  let stderr = "", browser;
  fixture.stderr.on("data", chunk => { stderr += chunk; });
  try {
    const port = await new Promise((resolve, reject) => {
      const lines = createInterface({ input: fixture.stdout });
      const timeout = setTimeout(() => reject(new Error("Fixture failed to start: " + stderr)), 15000);
      fixture.once("error", reject);
      fixture.once("exit", () => reject(new Error("Fixture exited: " + stderr)));
      lines.once("line", line => { clearTimeout(timeout); resolve(JSON.parse(line).port); });
    });
    const url = `http://127.0.0.1:${port}`;
    browser = await chromium.launch({ headless: true,
      ...(process.env.AGENT_VIEW_BROWSER_EXECUTABLE ? { executablePath: process.env.AGENT_VIEW_BROWSER_EXECUTABLE } : {}) });
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await context.newPage(), errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.goto(url);
    await until(async () => await page.locator("#mapNodes [data-node]").count() === 20, "initial bounded page");
    assert.match(await page.locator("#mapTitle").innerText(), /测试会话 0/);
    assert.match(await page.locator("#finishedSummary").innerText(), /179/);
    console.log("PASS: 250 direct children render at most 20 nodes and aggregate ended branches");

    await page.locator("#filter-all").click();
    await page.locator("#agentsNext").click();
    assert.equal(await page.locator("#pageJump").inputValue(), "2");
    assert.equal(await page.locator("#mapNodes [data-node]").first().getAttribute("data-node"), "agent-020");
    await page.locator("#pageJump").fill("13");
    await page.locator("#pageJump").press("Tab");
    await until(async () => await page.locator("#mapNodes [data-node]").count() === 10, "last sibling page");
    console.log("PASS: sibling pagination reaches the last child without expanding the full tree");

    await page.locator("#agentQuery").fill("稀有的深层任务");
    await until(async () => await page.locator("#mapNodes [data-node]").count() === 1, "global deep search");
    assert.equal(await page.locator("#mapNodes [data-node]").first().getAttribute("data-node"), "deep-7");
    await page.getByRole("button", { name: "定位到层级", exact: true }).click();
    assert.match(await page.locator("#parentLabel").innerText(), /deep-6/);
    assert.match(await page.locator("#breadcrumbs").innerText(), /deep-0/);
    assert.equal(await page.locator("#agentQuery").inputValue(), "");
    console.log("PASS: whole-session search locates an eighth-level descendant with its real ancestry");

    await page.locator("#backToMain").click();
    assert.equal(await page.locator("#pageJump").inputValue(), "13");
    await page.locator("#pageJump").fill("1");
    await page.locator("#pageJump").press("Tab");
    await page.locator("#graphView").click();
    assert.equal(await page.locator("#mapNodes [data-node]").count(), 20);
    await page.locator("#mapViewport").evaluate(el => { el.scrollTop = el.scrollHeight; });
    const parentBox = await page.locator(".branch-nav").boundingBox();
    assert.ok(parentBox.y >= 40 && parentBox.y + parentBox.height < 900);
    assert.ok(await page.locator("#parentRecord").isVisible());
    console.log("PASS: focused graph keeps parent and breadcrumb visible while children scroll");

    await page.locator("#listView").click();
    await page.locator("#agentsNext").click();
    const ids = await page.locator("#mapNodes [data-node]").evaluateAll(els => els.map(el => el.dataset.node));
    await page.locator("#mapViewport").evaluate(el => { el.scrollTop = 150; });
    const beforeScroll = await page.locator("#mapViewport").evaluate(el => el.scrollTop);
    await page.locator("#agentsNext").focus();
    const response = await context.request.post(url + "/__test__/update");
    assert.equal(response.status(), 200);
    await page.waitForTimeout(900);
    assert.deepEqual(await page.locator("#mapNodes [data-node]").evaluateAll(els => els.map(el => el.dataset.node)), ids);
    assert.equal(await page.locator("#pageJump").inputValue(), "2");
    assert.equal(await page.evaluate(() => document.activeElement.id), "agentsNext");
    assert.ok(Math.abs(await page.locator("#mapViewport").evaluate(el => el.scrollTop) - beforeScroll) < 2);
    console.log("PASS: SSE preserves page, sibling order, keyboard focus and reading position");

    await page.locator("#agentQuery").fill("agent-004");
    await page.locator("#mapNodes [data-node='agent-004'] .agent-select").click();
    await until(async () => (await page.locator("#log").innerText()).includes("agent-004"), "selected transcript");
    await page.locator("#closeDetail").click();
    await page.locator("#sessionQuery").fill("测试会话 999");
    await until(async () => await page.locator("[data-session='session-0999']").count() === 1, "search beyond latest 40");
    await page.locator("[data-session='session-0999']").click();
    await until(async () => (await page.locator("#mapTitle").innerText()) === "测试会话 999", "correct session click handler");
    console.log("PASS: historical project search and keyed session selection reach session 999");

    await page.locator("#currentSession").click();
    await until(async () => await page.locator("#sessionQuery").inputValue() === "", "return to current session");
    await until(async () => await page.locator("[data-session='session-0999']").count() === 1, "current session on second session page");
    assert.equal(await page.locator("#projectsPage").innerText(), "2 / 2");
    await page.locator("#graphView").click();
    await page.locator("#agentsNext").click();
    await page.reload();
    await until(async () => await page.locator("#mapNodes [data-node]").count() > 0, "reload saved navigation");
    assert.match(await page.locator("#mapTitle").innerText(), /测试会话 999/);
    assert.equal(await page.locator("#graphView").getAttribute("aria-pressed"), "true");
    assert.equal(await page.locator("#pageJump").inputValue(), "2");
    console.log("PASS: current-session location and saved per-session navigation survive reload");

    const narrow = await browser.newContext({ viewport: { width: 360, height: 740 } });
    const pane = await narrow.newPage();
    pane.on("pageerror", error => errors.push(error.message));
    await pane.goto(url + "/?embedded=1");
    await until(async () => await pane.locator("#mapNodes [data-node]").count() === 20, "narrow isolated pane");
    assert.equal(await pane.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await pane.locator("#parentRecord").click();
    await until(async () => (await pane.locator("#log").innerText()).includes("main"), "narrow transcript");
    await pane.locator("#closeDetail").click();
    assert.ok(await pane.locator("#mapCounts").isVisible());
    assert.equal(await pane.locator("#sessions").isVisible(), false);
    assert.deepEqual(errors, []);
    console.log("PASS: isolated 360px Desktop pane retains navigation, transcript return and CSP compatibility");
    await narrow.close();
    if (process.env.AGENT_VIEW_TEST_SCREENSHOT) await page.screenshot({ path: process.env.AGENT_VIEW_TEST_SCREENSHOT, fullPage: true });
    await context.close();
  } finally {
    if (browser) await browser.close();
    fixture.kill();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
