"use strict";

const assert = require("node:assert/strict");
const { test } = require("node:test");
const slider = require("../slider.js");

const SN = "1581F4XFC123456";
const PAGE = "https://repair.dji.com/device/detail?re=cn&lang=zh-CN";

function random(seed) {
  return () => {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
    return seed / 0x100000000;
  };
}

function fixture(overrides = {}) {
  return {
    url: PAGE, sn: SN, kind: "slider",
    handle: { x: 200, y: 300, width: 48, height: 48 },
    rail: { x: 200, y: 300, width: 390, height: 48 },
    viewport: { width: 1440, height: 1000 },
    ...overrides
  };
}

function harness(overrides = {}) {
  let clock = 0;
  let released = false;
  const calls = [];
  const options = {
    sn: SN,
    rng: random(123),
    now: () => clock,
    sleep: async (ms) => { clock += ms; },
    attach: async () => { calls.push(["attach"]); },
    detach: async () => { calls.push(["detach"]); },
    send: async (method, params) => {
      calls.push([method, params]);
      if (params.type === "mouseReleased") released = true;
    },
    read: async () => fixture({ kind: released ? "passed" : "slider" }),
    ...overrides
  };
  return { options, calls, solve: () => slider.solve(options) };
}

test("only the DJI device HTTPS origin is accepted", () => {
  assert.equal(slider.isAllowedPage(PAGE), true);
  for (const url of ["http://repair.dji.com/device/detail", "https://repair.dji.com.evil.test/device/detail",
    "https://repair.dji.com/devices/detail", "https://example.com/device/detail", "not a URL"]) {
    assert.equal(slider.isAllowedPage(url), false, url);
  }
});

test("trajectories stay finite and inside the rail and reach the endpoint", () => {
  for (const variant of ["feedback", "baseline_current"]) {
    for (const distance of [8, 10, 11, 48, 342, 1000, 2000]) {
      for (let seed = 1; seed <= 40; seed += 1) {
        const track = slider.generateDrag(distance, { rng: random(seed), variant });
        let x = 0;
        let duration = 0;
        for (const point of track) {
          assert.equal(point.length, 3);
          assert.ok(point.every(Number.isFinite), `${variant}, ${distance}, seed ${seed}`);
          assert.ok(point[2] > 0);
          x += point[0];
          duration += point[2];
          assert.ok(x >= -1e-7 && x <= distance + 1e-7);
        }
        assert.ok(Math.abs(x - distance) < 1e-7);
        assert.ok(duration > 0 && duration <= 4000 + 1e-7);
      }
    }
  }
});

test("trajectory options reject invalid distances, sampling, and durations", () => {
  for (const value of [NaN, Infinity, -1, 0, 7, 2001]) {
    assert.throws(() => slider.generateDrag(value), { code: "invalid_distance" });
  }
  assert.throws(() => slider.generateDrag(342, { events: 19 }), { code: "invalid_events" });
  assert.throws(() => slider.generateDrag(342, { totalSeconds: 5 }), { code: "invalid_duration" });
  assert.throws(() => slider.generateDrag(342, { variant: "unknown" }), { code: "invalid_variant" });
});

test("a successful drag waits for the page verdict and releases before detaching", async () => {
  const run = harness();
  assert.deepEqual(await run.solve(), { ok: true, passed: true });
  const mouse = run.calls.filter(([method]) => method === "Input.dispatchMouseEvent").map(([, params]) => params);
  assert.equal(mouse.filter((event) => event.type === "mousePressed").length, 1);
  assert.equal(mouse.filter((event) => event.type === "mouseReleased").length, 1);
  assert.equal(mouse.at(-1).x, 566);
  assert.equal(mouse.at(-1).buttons, 0);
  assert.equal(run.calls.at(-1)[0], "detach");
});

test("an already verified page does not receive mouse events", async () => {
  const run = harness({ read: async () => fixture({ kind: "passed" }) });
  assert.equal((await run.solve()).ok, true);
  assert.deepEqual(run.calls, [["attach"], ["detach"]]);
});

test("a different SN or origin is rejected before pressing", async () => {
  for (const state of [fixture({ sn: "1581F4XFC654321" }), fixture({ url: "https://example.com/device/detail" })]) {
    const run = harness({ read: async () => state });
    assert.equal((await run.solve()).code, "page_changed");
    assert.deepEqual(run.calls, [["attach"], ["detach"]]);
  }
});

test("layout changes during the approach are rejected before pressing", async () => {
  let reads = 0;
  const run = harness({ read: async () => fixture({
    handle: { x: ++reads > 1 ? 210 : 200, y: 300, width: 48, height: 48 }
  }) });
  assert.equal((await run.solve()).code, "layout_changed");
  assert.equal(run.calls.some(([, event]) => event?.type === "mousePressed"), false);
  assert.equal(run.calls.at(-1)[0], "detach");
});

test("cancellation during a drag always releases the pressed mouse", async () => {
  let cancelled = false;
  const run = harness({ cancelled: () => cancelled });
  const send = run.options.send;
  run.options.send = async (method, params) => {
    await send(method, params);
    if (params.type === "mousePressed") cancelled = true;
  };
  assert.equal((await run.solve()).code, "cancelled");
  assert.equal(run.calls.at(-2)[1].type, "mouseReleased");
  assert.equal(run.calls.at(-1)[0], "detach");
});

test("a rejected press still sends a release before cleanup", async () => {
  const run = harness();
  const send = run.options.send;
  run.options.send = async (method, params) => {
    await send(method, params);
    if (params.type === "mousePressed") throw new Error("command timed out");
  };
  assert.equal((await run.solve()).code, "debugger_error");
  assert.equal(run.calls.at(-2)[1].type, "mouseReleased");
  assert.equal(run.calls.at(-1)[0], "detach");
});

test("an unconfirmed drag is never reported as verification success", async () => {
  const run = harness({ read: async () => fixture() });
  assert.equal((await run.solve()).code, "unconfirmed");
  assert.equal(run.calls.at(-1)[0], "detach");
});

test("a failed or unsupported challenge requires manual completion", async () => {
  for (const kind of ["failed", "unsupported", "covered"]) {
    const run = harness({ read: async () => fixture({ kind, message: "manual completion required" }) });
    const result = await run.solve();
    assert.equal(result.ok, false);
    assert.equal(result.code, kind);
    assert.deepEqual(run.calls, [["attach"], ["detach"]]);
  }
});

test("dragging near the viewport edge keeps every mouse position visible", async () => {
  let released = false;
  const run = harness({ read: async () => fixture({ kind: released ? "passed" : "slider",
    handle: { x: 200, y: 0, width: 48, height: 48 },
    rail: { x: 200, y: 0, width: 390, height: 48 }
  }) });
  const send = run.options.send;
  run.options.send = async (method, params) => {
    assert.ok(params.y >= 8 && params.y <= 992, `mouse y=${params.y}`);
    await send(method, params);
    if (params.type === "mouseReleased") released = true;
  };
  assert.equal((await run.solve()).ok, true);
});
