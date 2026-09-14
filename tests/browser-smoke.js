"use strict";

// Load the real MV3 extension in an isolated Chromium profile. Every website
// request is fulfilled locally or blocked; no serial number is sent to DJI.
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { chromium } = require("playwright");

const ROOT = path.resolve(__dirname, "..");
const KEY = "djiSnBatchCheckerStateV1";
const URL = "https://repair.dji.com/device/detail?re=cn&lang=zh-CN";
const SN1 = "1581F4XFC123456";
const SN2 = "1581F4XFC654321";

async function main() {
  const html = await fs.readFile(path.join(__dirname, "fixtures", "device.html"), "utf8");
  const context = await chromium.launchPersistentContext("", {
    channel: "chromium",
    headless: true,
    viewport: { width: 1440, height: 1000 },
    locale: "zh-CN",
    args: [`--disable-extensions-except=${ROOT}`, `--load-extension=${ROOT}`]
  });
  const failures = [];
  let page;
  try {
    await context.route("**/*", async (route) => {
      const url = new globalThis.URL(route.request().url());
      if (url.origin === "https://repair.dji.com" && url.pathname.startsWith("/device/")) {
        await route.fulfill({ status: 200, contentType: "text/html; charset=utf-8", body: html });
      } else if (url.protocol === "chrome-extension:") {
        await route.continue();
      } else {
        await route.abort();
      }
    });
    const worker = context.serviceWorkers()[0] || await context.waitForEvent("serviceworker");
    async function state() {
      return worker.evaluate(async (key) => (await chrome.storage.local.get(key))[key], KEY);
    }
    async function waitState(predicate, description, timeout = 16000) {
      const deadline = Date.now() + timeout;
      let last;
      while (Date.now() < deadline) {
        last = await state();
        if (predicate(last)) return last;
        await new Promise((resolve) => setTimeout(resolve, 100));
      }
      assert.fail(`${description}; last state: ${JSON.stringify(last)}`);
    }
    async function open(options = {}, query = "") {
      if (page) await page.close();
      await worker.evaluate(async ({ key, options }) => {
        await chrome.storage.local.set({ [key]: {
          items: [], currentId: null, running: false, collapsed: false, importText: "",
          autoAdvance: true, autoSlider: true, ...options
        } });
      }, { key: KEY, options });
      page = await context.newPage();
      page.on("pageerror", (error) => failures.push(error.message));
      page.setDefaultTimeout(12000);
      await page.goto(URL + query);
      await page.locator("[data-import]").waitFor();
    }
    async function add(sns) {
      await page.locator("[data-import]").fill(sns);
      await page.locator('[data-action="add"]').click();
    }
    async function start() { await page.locator('[data-action="start"]').click(); }
    async function test(name, run) {
      await run();
      console.log(`PASS ${name}`);
    }

    await test("a running queue resumes when the form appears late after refresh", async () => {
      for (const status of ["ready", "querying"]) {
        await open({ running: true, currentId: "restored-item", items: [
          { id: "restored-item", sn: SN1, status }
        ] }, "&formDelay=1500");
        await waitState((value) => value?.items[0]?.status === "activated" && !value.running, `delayed form resumes ${status}`);
        assert.deepEqual(await page.evaluate(() => fixture.queries), [SN1]);
      }
    });

    await test("a refresh after saving a result advances without re-querying it", async () => {
      await open({ running: true, currentId: "finished-item", items: [
        { id: "finished-item", sn: SN1, status: "activated", activationTime: "2025-01-01" },
        { id: "next-item", sn: SN2, status: "pending" }
      ] }, "&formDelay=1500");
      const saved = await waitState((value) => value?.items[1]?.status === "activated" && !value.running, "restored queue advances");
      assert.equal(saved.items[0].activationTime, "2025-01-01");
      assert.deepEqual(await page.evaluate(() => fixture.queries), [SN2]);
    });

    await test("a manual query for another SN cannot mark the queued SN as failed", async () => {
      await open({ autoSlider: false, autoAdvance: false });
      await add(SN1);
      await start();
      await page.locator('input[placeholder*="序列号"]').fill(SN2);
      await page.evaluate(() => { fixture.error = "序列号不存在"; });
      await page.locator(".btn-step button").click();
      await page.waitForTimeout(1200);
      const saved = await state();
      assert.equal(saved.items[0].sn, SN1);
      assert.equal(saved.items[0].status, "ready");
      assert.equal(saved.running, false);
    });

    await test("real extension drags, records, advances, and exports two serial numbers", async () => {
      await open();
      await add(`${SN1}\n${SN2}\n${SN1}\nBAD-SN`);
      assert.equal((await state()).items.length, 2);
      await start();
      const saved = await waitState((value) => value?.items.length === 2
        && value.items.every((item) => item.status === "activated") && !value.running, "both results recorded");
      assert.deepEqual(saved.items.map((item) => item.activationTime), ["2026-06-27", "2026-06-27"]);
      const observed = await page.evaluate(() => ({ queries: fixture.queries, mouse: fixture.mouse, attempts: fixture.attempts }));
      assert.deepEqual(observed.queries, [SN1, SN2]);
      assert.equal(observed.attempts, 2);
      assert.equal(observed.mouse.filter((event) => event.type === "up").length, 2);
      assert.ok(observed.mouse.every((event) => event.trusted));
      const downloadPromise = page.waitForEvent("download");
      await page.locator('[data-action="export"]').click();
      const download = await downloadPromise;
      const csv = await fs.readFile(await download.path(), "utf8");
      assert.ok(csv.startsWith("\ufeffSN,查询状态"));
      assert.ok(csv.includes(`${SN1},已激活,DJI Mini 4 Pro,2026-06-27`));
      assert.ok(csv.includes(`${SN2},已激活,DJI Mini 4 Pro,2026-06-27`));
      await fs.mkdir(path.join(ROOT, "test-results"), { recursive: true });
      await page.screenshot({ path: path.join(ROOT, "test-results", "browser-smoke.png") });
    });

    await test("failed verification waits for a user retry, then continues", async () => {
      await open();
      await page.evaluate(() => { fixture.mode = "fail"; });
      await add(SN1);
      await start();
      await page.locator('[data-action="retry-slider"]').waitFor();
      assert.equal((await state()).items[0].status, "ready");
      await page.waitForTimeout(1200);
      assert.equal(await page.evaluate(() => fixture.attempts), 1);
      assert.deepEqual(await page.evaluate(() => fixture.queries), []);
      await page.evaluate(() => { fixture.mode = "pass"; });
      await page.locator('[data-action="retry-slider"]').click();
      await waitState((value) => value?.items[0]?.status === "activated" && !value.running, "retry records a result");
      assert.equal(await page.evaluate(() => fixture.attempts), 2);
    });

    await test("pause during a drag releases the mouse and ignores the stale response", async () => {
      await open();
      await add(`${SN1}\n${SN2}`);
      await start();
      await page.waitForFunction(() => fixture.pressed);
      // Dispatch the pause without moving the test runner's own mouse while
      // the extension is dragging; this exercises the actual UI click handler.
      await page.locator('[data-action="pause"]').evaluate((button) => button.click());
      await page.waitForFunction(() => !fixture.pressed);
      await page.waitForTimeout(1200);
      const saved = await state();
      assert.equal(saved.running, false);
      assert.equal(saved.items[0].status, "ready");
      assert.equal(saved.items[1].status, "pending");
      assert.deepEqual(await page.evaluate(() => fixture.queries), []);
      const connection = await worker.evaluate(async (url) => {
        const target = (await chrome.debugger.getTargets()).find((entry) => entry.url === url && entry.type === "page");
        try {
          await chrome.debugger.sendCommand({ tabId: target.tabId }, "Runtime.evaluate", { expression: "true" });
          return "still attached";
        } catch (error) { return error.message; }
      }, URL);
      // getTargets().attached also counts Playwright's own CDP session.
      // This command specifically tests the extension's connection instead.
      assert.match(connection, /not attached/i);
    });

    await test("manual verification and an inactive device work with automation disabled", async () => {
      await open({ autoSlider: false, autoAdvance: false });
      await add(SN1);
      await start();
      await page.evaluate(() => { fixture.resultStatus = "inactive"; fixture.verify(); });
      await page.waitForTimeout(1200);
      assert.equal(await page.evaluate(() => fixture.attempts), 0);
      assert.deepEqual(await page.evaluate(() => fixture.queries), []);
      await page.locator(".btn-step button").click();
      const saved = await waitState((value) => value?.items[0]?.status === "inactive", "manual query records inactive device");
      assert.equal(saved.running, false);
    });

    await test("refresh restores the saved queue and both automation settings", async () => {
      await open({ autoSlider: false, autoAdvance: false });
      await add(`${SN1}\n${SN2}`);
      await start();
      await page.locator('[data-action="pause"]').click();
      await page.reload();
      await page.locator("[data-import]").waitFor();
      assert.equal(await page.locator(".sn-item").count(), 2);
      assert.equal(await page.locator("[data-auto-slider]").isChecked(), false);
      assert.equal(await page.locator("[data-auto-advance]").isChecked(), false);
      assert.equal((await state()).running, false);
    });

    assert.deepEqual(failures, [], "no uncaught page or content script errors");
    console.log("Browser smoke tests passed (local fixtures; no live CAPTCHA verdict tested).");
  } catch (error) {
    if (page && !page.isClosed()) {
      await fs.mkdir(path.join(ROOT, "test-results"), { recursive: true });
      await page.screenshot({ path: path.join(ROOT, "test-results", "browser-failure.png") }).catch(() => {});
    }
    throw error;
  } finally {
    await context.close();
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
