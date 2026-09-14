"use strict";

const assert = require("node:assert/strict");
const { test } = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const slider = require("../slider.js");

const PAGE = "https://repair.dji.com/device/detail?re=cn&lang=zh-CN";
const SN = "1581F4XFC123456";
const source = fs.readFileSync(path.join(__dirname, "..", "background.js"), "utf8");
const nextTurn = () => new Promise((resolve) => setImmediate(resolve));

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function harness(options = {}) {
  const handlers = {};
  const calls = [];
  const timers = new Map();
  let timerId = 0;
  const listen = (name) => ({ addListener: (handler) => { handlers[name] = handler; } });
  const chrome = {
    runtime: { id: "local-test-extension", onMessage: listen("message") },
    debugger: {
      onDetach: listen("detach"), onEvent: listen("event"),
      attach: async (target) => { calls.push(["attach", target]); await options.attach?.(); },
      detach: async (target) => { calls.push(["detach", target]); handlers.detach(target); },
      sendCommand: async (target, method, params) => {
        calls.push([method, target, params]);
        if (options.send) return options.send(target, method, params);
        return { result: { value: { url: PAGE, sn: SN, kind: "passed" } } };
      }
    },
    tabs: { get: async () => ({ url: PAGE }), onRemoved: listen("removed") }
  };
  const context = vm.createContext({
    chrome, DJISNSlider: slider, importScripts() {},
    setTimeout: (callback) => { timers.set(++timerId, callback); return timerId; },
    clearTimeout: (id) => timers.delete(id)
  });
  vm.runInContext(source, context, { filename: "background.js" });
  const sender = { id: chrome.runtime.id, frameId: 0, url: PAGE, tab: { id: 7, url: PAGE } };
  const request = { type: "DJI_SLIDER_SOLVE", requestId: "request-12345", sn: SN };
  function message(data = request, from = sender) {
    return new Promise((resolve) => handlers.message(data, from, resolve));
  }
  return { calls, context, handlers, message, request, sender, timers };
}

test("background rejects untrusted frames, origins, and request parameters", async () => {
  const run = harness();
  for (const sender of [
    { ...run.sender, id: "other-extension" }, { ...run.sender, frameId: 1 },
    { ...run.sender, url: "https://example.com/device/detail" },
    { ...run.sender, tab: { id: 7, url: "https://example.com/device/detail" } }
  ]) {
    assert.equal((await run.message(run.request, sender)).code, "invalid_sender");
  }
  assert.equal((await run.message({ ...run.request, sn: "BAD" })).code, "invalid_request");
  assert.equal((await run.message({ ...run.request, requestId: "!" })).code, "invalid_request");
  assert.equal(run.calls.length, 0);
});

test("background releases a successful debugger connection exactly once", async () => {
  const run = harness();
  assert.equal((await run.message()).ok, true);
  assert.deepEqual(run.calls.map(([method]) => method), ["attach", "Page.enable", "Runtime.evaluate", "detach"]);
  assert.equal(vm.runInContext("jobs.size", run.context), 0);
  assert.equal(run.timers.size, 0);
});

test("background serializes jobs and only the matching cancellation stops a request", async () => {
  const attached = deferred();
  const run = harness({ attach: () => attached.promise });
  const first = run.message();
  await nextTurn();
  assert.equal((await run.message({ ...run.request, requestId: "request-other" })).code, "busy");
  await run.message({ type: "DJI_SLIDER_CANCEL", requestId: "request-other" });
  assert.equal(vm.runInContext("jobs.get(7).cancelled", run.context), false);
  await run.message({ type: "DJI_SLIDER_CANCEL", requestId: run.request.requestId });
  attached.resolve();
  assert.equal((await first).code, "cancelled");
  assert.equal(run.calls.at(-1)[0], "detach");
  assert.equal(vm.runInContext("jobs.size", run.context), 0);
});

test("a connection is released if enabling Page events fails", async () => {
  const run = harness({ send: async () => { throw new Error("Page.enable rejected"); } });
  assert.equal((await run.message()).ok, false);
  assert.deepEqual(run.calls.map(([method]) => method), ["attach", "Page.enable", "detach"]);
  assert.equal(vm.runInContext("jobs.size", run.context), 0);
});

test("navigation or closing the tab cancels pending work", async () => {
  for (const event of ["navigation", "close"]) {
    const attached = deferred();
    const run = harness({ attach: () => attached.promise });
    const result = run.message();
    await nextTurn();
    if (event === "navigation") run.handlers.event({ tabId: 7 }, "Page.frameNavigated", { frame: { id: "main" } });
    else run.handlers.removed(7);
    attached.resolve();
    assert.equal((await result).code, "cancelled");
    assert.equal(run.calls.at(-1)[0], "detach");
  }
});

test("an attachment completing after its timeout is still detached", async () => {
  const attached = deferred();
  const run = harness({ attach: () => attached.promise });
  const result = run.message();
  await nextTurn();
  assert.equal(run.timers.size, 1);
  [...run.timers.values()][0]();
  assert.equal((await result).code, "timeout");
  assert.equal(vm.runInContext("jobs.size", run.context), 0);
  attached.resolve();
  await nextTurn();
  assert.deepEqual(run.calls.map(([method]) => method), ["attach", "detach"]);
});
